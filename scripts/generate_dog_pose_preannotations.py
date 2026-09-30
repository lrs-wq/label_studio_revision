from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/root/label_studio"))
DATA_DIR = Path(os.getenv("BASE_DATA_DIR", str(PROJECT_ROOT / "label_studio_data")))
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
PROJECT_TITLE = os.getenv("DOG_POSE_PROJECT_TITLE", "dog_pose_frames_every_50_all")
ML_URL = os.getenv("DOG_POSE_ML_URL", "http://127.0.0.1:9094")
MODEL_VERSION = os.getenv("DOG_POSE_MODEL_VERSION", "dog_pose_yolo26m_best")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "https://ai.hzzootech.com")
BATCH_SIZE = int(os.getenv("DOG_POSE_PREDICT_BATCH_SIZE", "8"))
HTTP_TIMEOUT = int(os.getenv("DOG_POSE_PREDICT_HTTP_TIMEOUT", "600"))
RESET_PREDICTIONS = os.getenv("DOG_POSE_RESET_PREDICTIONS", "false").lower() in {
    "1",
    "true",
    "yes",
}


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def configure_environment() -> None:
    import label_studio

    sys.path.insert(0, str(Path(label_studio.__file__).resolve().parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["HOST"] = PUBLIC_HOST
    os.environ["COLLECT_ANALYTICS"] = "false"
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.is_file():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_dog_pose_predictions_{timestamp}.sqlite3"
    if backup_path.exists():
        raise FileExistsError(backup_path)
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    log(f"Database backup: {backup_path}")
    return backup_path


def chunks(items: list[int], size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def request_predictions(project, task_batch, serializer) -> list[dict]:
    payload = {
        "tasks": serializer(task_batch, many=True).data,
        "project": str(project.id),
        "label_config": project.label_config,
    }
    last_error = None
    for attempt in range(1, 4):
        try:
            response = requests.post(f"{ML_URL}/predict", json=payload, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
            results = response.json().get("results")
            if not isinstance(results, list) or len(results) != len(task_batch):
                raise RuntimeError(f"Invalid backend response for {len(task_batch)} tasks")
            return results
        except Exception as error:
            last_error = error
            if attempt < 3:
                log(f"Prediction request retry {attempt}/3: {error}")
                time.sleep(attempt * 2)
    raise RuntimeError(f"Prediction request failed: {last_error}")


def main() -> None:
    if BATCH_SIZE < 1:
        raise ValueError("DOG_POSE_PREDICT_BATCH_SIZE must be positive")

    health_response = requests.get(f"{ML_URL}/health", timeout=60)
    health_response.raise_for_status()
    health = health_response.json()
    if health.get("status") != "UP" or health.get("model_version") != MODEL_VERSION:
        raise RuntimeError(f"Unexpected dog pose backend: {health}")

    configure_environment()
    import django

    django.setup()

    from django.db import transaction
    from django.utils import timezone
    from ml.models import MLBackend, MLBackendState
    from projects.models import Project
    from tasks.functions import update_tasks_counters
    from tasks.models import Prediction, Task
    from tasks.serializers import TaskSimpleSerializer

    project = Project.objects.get(title=PROJECT_TITLE)
    backend, _ = MLBackend.objects.update_or_create(
        project=project,
        url=ML_URL,
        defaults={
            "title": MODEL_VERSION,
            "model_version": MODEL_VERSION,
            "state": MLBackendState.CONNECTED,
            "error_message": None,
            "is_interactive": False,
            "timeout": float(HTTP_TIMEOUT),
            "auto_update": False,
            "auth_method": "NONE",
            "basic_auth_user": "",
            "basic_auth_pass": "",
            "description": "Docker YOLO dog detection with selected 17-keypoint pose output",
            "extra_params": {},
        },
    )
    project.model_version = MODEL_VERSION
    project.show_collab_predictions = True
    project.save(update_fields=["model_version", "show_collab_predictions"])
    requests.post(
        f"{ML_URL}/setup",
        json={"project": project.id, "schema": project.label_config, "hostname": PUBLIC_HOST},
        timeout=60,
    ).raise_for_status()

    backup_database()
    if RESET_PREDICTIONS:
        with transaction.atomic():
            deleted = Prediction.objects.filter(project=project).delete()[0]
            Task.objects.filter(project=project).update(total_predictions=0)
        log(f"Deleted {deleted} old prediction-related rows")

    existing_task_ids = set(
        Prediction.objects.filter(project=project, model_version=MODEL_VERSION).values_list(
            "task_id", flat=True
        )
    )
    all_task_ids = list(
        Task.objects.filter(project=project).order_by("id").values_list("id", flat=True)
    )
    pending_task_ids = [task_id for task_id in all_task_ids if task_id not in existing_task_ids]
    log(
        f"Project {project.id} {project.title}: total={len(all_task_ids)}, "
        f"existing={len(existing_task_ids)}, pending={len(pending_task_ids)}"
    )

    created = 0
    backend_errors = 0
    nonempty = 0
    region_types: Counter[str] = Counter()
    started = time.time()

    for batch_number, id_batch in enumerate(chunks(pending_task_ids, BATCH_SIZE), start=1):
        task_batch = list(Task.objects.filter(id__in=id_batch).order_by("id"))
        try:
            results = request_predictions(project, task_batch, TaskSimpleSerializer)
        except Exception as error:
            if len(task_batch) > 1:
                log(f"Batch {batch_number} failed; retrying tasks individually: {error}")
                results = []
                for task in task_batch:
                    try:
                        results.extend(request_predictions(project, [task], TaskSimpleSerializer))
                    except Exception as task_error:
                        log(f"Task {task.id} failed; storing empty prediction: {task_error}")
                        results.append(
                            {
                                "result": [],
                                "score": 0.0,
                                "model_version": MODEL_VERSION,
                                "error": str(task_error),
                            }
                        )
            else:
                log(f"Task {task_batch[0].id} failed; storing empty prediction: {error}")
                results = [
                    {
                        "result": [],
                        "score": 0.0,
                        "model_version": MODEL_VERSION,
                        "error": str(error),
                    }
                ]

        now = timezone.now()
        prediction_objects = []
        for task, result in zip(task_batch, results):
            regions = result.get("result") or []
            if result.get("error"):
                backend_errors += 1
            if regions:
                nonempty += 1
            region_types.update(region.get("type", "unknown") for region in regions)
            prediction_objects.append(
                Prediction(
                    task_id=task.id,
                    project_id=project.id,
                    model_id=backend.id,
                    result=regions,
                    score=result.get("score"),
                    model_version=result.get("model_version") or MODEL_VERSION,
                    created_at=now,
                    updated_at=now,
                )
            )

        with transaction.atomic():
            Prediction.objects.bulk_create(prediction_objects, batch_size=BATCH_SIZE)
            Task.objects.filter(id__in=id_batch).update(total_predictions=1)
        created += len(prediction_objects)

        if batch_number == 1 or created % 80 == 0 or created == len(pending_task_ids):
            elapsed = time.time() - started
            rate = created / elapsed if elapsed else 0.0
            remaining = (len(pending_task_ids) - created) / rate if rate else 0.0
            log(
                f"Progress {created}/{len(pending_task_ids)} at {rate:.2f} images/s; "
                f"ETA {remaining / 60:.1f} min; nonempty={nonempty}; errors={backend_errors}"
            )

    updated = update_tasks_counters(Task.objects.filter(project=project), from_scratch=True)
    final_count = Prediction.objects.filter(project=project, model_version=MODEL_VERSION).count()
    total_prediction_sum = sum(
        Task.objects.filter(project=project).values_list("total_predictions", flat=True)
    )
    log(
        f"Done: predictions={final_count}, task_prediction_sum={total_prediction_sum}, "
        f"counter_updates={updated}, backend_errors={backend_errors}, "
        f"new_nonempty={nonempty}, new_region_types={dict(region_types)}"
    )


if __name__ == "__main__":
    main()
