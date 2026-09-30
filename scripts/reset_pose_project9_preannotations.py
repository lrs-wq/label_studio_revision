import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import requests


LABEL_STUDIO_SITE_PACKAGES = r"D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio"
BASE_DATA_DIR = r"D:\gogelila\label_studio\label_studio_data"
DB_PATH = Path(BASE_DATA_DIR) / "label_studio.sqlite3"
ML_URL = os.environ.get("POSE_ML_URL", "http://127.0.0.1:9091")
MODEL_VERSION = os.environ.get("POSE_MODEL_VERSION", "cat_pose_trainmap_noaug_300e_best")
TARGET_PROJECT_ID = int(os.environ.get("TARGET_PROJECT_ID", "9"))
EXPECTED_PROJECT_TITLE = os.environ.get("EXPECTED_PROJECT_TITLE", "cat_pose_annotation_seed_800")
PREDICT_BATCH_SIZE = int(os.environ.get("PREDICT_BATCH_SIZE", "8"))
HTTP_TIMEOUT = int(os.environ.get("PREDICT_HTTP_TIMEOUT", "180"))


sys.path.insert(0, LABEL_STUDIO_SITE_PACKAGES)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
os.environ["BASE_DATA_DIR"] = BASE_DATA_DIR
os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = BASE_DATA_DIR
os.environ["HOST"] = "http://127.0.0.1:8090"
os.environ["LATEST_VERSION_CHECK"] = "false"

secret_path = Path(BASE_DATA_DIR) / "secret_key.txt"
if secret_path.is_file():
    os.environ["SECRET_KEY"] = secret_path.read_text(encoding="utf-8").strip()

import django  # noqa: E402

django.setup()

from django.db import connection, transaction  # noqa: E402
from django.utils import timezone  # noqa: E402
from ml.models import MLBackend, MLBackendState  # noqa: E402
from projects.models import Project  # noqa: E402
from tasks.functions import update_tasks_counters  # noqa: E402
from tasks.models import Prediction, Task  # noqa: E402
from tasks.serializers import TaskSimpleSerializer  # noqa: E402


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def retry(label, func, attempts=8, delay=1.0):
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            text = str(exc).lower()
            if "database is locked" not in text and "database table is locked" not in text:
                raise
            if attempt == attempts:
                raise
            log(f"{label}: database locked, retry {attempt}/{attempts}")
            time.sleep(delay * attempt)


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def backup_database():
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = DB_PATH.with_name(f"label_studio.before_pose_project9_preannotations_{timestamp}.sqlite3")
    shutil.copy2(DB_PATH, backup_path)
    log(f"Database backup: {backup_path}")
    return backup_path


def healthcheck():
    response = requests.get(f"{ML_URL}/health", timeout=60)
    response.raise_for_status()
    data = response.json()
    log(
        "ML health: "
        f"status={data.get('status')} task={data.get('task')} "
        f"model_version={data.get('model_version')} path={data.get('model_path')}"
    )
    if data.get("status") != "UP":
        raise RuntimeError(f"ML backend is not UP: {data}")
    if data.get("task") != "pose":
        raise RuntimeError(f"ML backend is not pose backend: {data}")
    if data.get("model_version") != MODEL_VERSION:
        raise RuntimeError(f"ML backend is not using {MODEL_VERSION}: {data}")


def ensure_backend(project):
    backend, created = MLBackend.objects.update_or_create(
        project=project,
        url=ML_URL,
        defaults={
            "title": MODEL_VERSION,
            "model_version": MODEL_VERSION,
            "state": MLBackendState.CONNECTED,
            "error_message": None,
            "is_interactive": False,
            "timeout": float(HTTP_TIMEOUT),
            "auto_update": True,
            "auth_method": "NONE",
            "basic_auth_user": "",
            "basic_auth_pass": "",
            "description": "YOLO pose preannotation backend",
            "extra_params": {},
        },
    )
    project.model_version = MODEL_VERSION
    project.show_collab_predictions = True
    project.save(update_fields=["model_version", "show_collab_predictions"])
    log(f"ML backend {'created' if created else 'updated'}: id={backend.id} url={backend.url}")
    return backend


def clear_old_predictions(project_id):
    before = Prediction.objects.filter(project_id=project_id).count()
    log(f"Old prediction rows before reset: {before}")

    def delete_sql():
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM prediction_meta WHERE prediction_id IN "
                    "(SELECT id FROM prediction WHERE project_id = %s)",
                    [project_id],
                )
                cursor.execute(
                    "UPDATE task_completion SET parent_prediction_id = NULL "
                    "WHERE parent_prediction_id IN "
                    "(SELECT id FROM prediction WHERE project_id = %s)",
                    [project_id],
                )
                cursor.execute("DELETE FROM prediction WHERE project_id = %s", [project_id])
                cursor.execute("UPDATE task SET total_predictions = 0 WHERE project_id = %s", [project_id])

    retry("clear old predictions", delete_sql)
    after = Prediction.objects.filter(project_id=project_id).count()
    log(f"Old prediction rows after reset: {after}")


def request_predictions(project, task_batch):
    serialized_tasks = TaskSimpleSerializer(task_batch, many=True).data
    payload = {
        "tasks": serialized_tasks,
        "project": str(project.id),
        "label_config": project.label_config,
    }
    response = requests.post(f"{ML_URL}/predict", json=payload, timeout=HTTP_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError(f"ML backend returned invalid response: {data}")
    if len(results) != len(task_batch):
        raise RuntimeError(f"ML backend returned {len(results)} results for {len(task_batch)} tasks")
    return results


def create_predictions(project, backend, task_batch, ml_results):
    now = timezone.now()
    prediction_objects = []
    for task, result in zip(task_batch, ml_results):
        prediction_objects.append(
            Prediction(
                task_id=task.id,
                project_id=project.id,
                model_id=backend.id,
                result=result.get("result", []),
                score=result.get("score"),
                model_version=result.get("model_version") or MODEL_VERSION,
                created_at=now,
                updated_at=now,
            )
        )

    retry(
        f"insert predictions project={project.id}",
        lambda: Prediction.objects.bulk_create(prediction_objects, batch_size=500),
    )
    return len(prediction_objects)


def predict_batch_with_split(project, backend, task_batch):
    try:
        ml_results = request_predictions(project, task_batch)
        return create_predictions(project, backend, task_batch, ml_results)
    except Exception as exc:
        if len(task_batch) <= 1:
            task = task_batch[0]
            log(f"Task {task.id} prediction failed, creating empty prediction: {exc}")
            return create_predictions(
                project,
                backend,
                task_batch,
                [{"result": [], "score": 0.0, "model_version": MODEL_VERSION}],
            )

        midpoint = len(task_batch) // 2
        log(f"Batch failed, size={len(task_batch)}; splitting. Error: {exc}")
        return predict_batch_with_split(project, backend, task_batch[:midpoint]) + predict_batch_with_split(
            project,
            backend,
            task_batch[midpoint:],
        )


def regenerate_for_project(project, backend):
    task_ids = list(Task.objects.filter(project_id=project.id).order_by("id").values_list("id", flat=True))
    total = len(task_ids)
    log(f"Project {project.id} {project.title}: regenerating pose predictions for {total} tasks")
    created = 0
    started = time.time()

    for batch_index, id_batch in enumerate(chunks(task_ids, PREDICT_BATCH_SIZE), start=1):
        task_batch = list(Task.objects.filter(id__in=id_batch).select_related("project").order_by("id"))
        created += predict_batch_with_split(project, backend, task_batch)
        if batch_index == 1 or created % 100 == 0 or created == total:
            elapsed = time.time() - started
            rate = created / elapsed if elapsed else 0
            log(f"Project {project.id}: {created}/{total} predictions created ({rate:.2f}/s)")

    updated = retry(
        f"update task counters project={project.id}",
        lambda: update_tasks_counters(Task.objects.filter(project_id=project.id), from_scratch=True),
    )
    final_predictions = Prediction.objects.filter(project_id=project.id, model_version=MODEL_VERSION).count()
    total_counter = Task.objects.filter(project_id=project.id).values_list("total_predictions", flat=True)
    log(
        f"Project {project.id}: done, predictions={final_predictions}, "
        f"updated_task_counters={updated}, task_prediction_sum={sum(total_counter)}"
    )


def main():
    backup_database()
    healthcheck()

    project = Project.objects.get(pk=TARGET_PROJECT_ID)
    if EXPECTED_PROJECT_TITLE and project.title != EXPECTED_PROJECT_TITLE:
        raise RuntimeError(f"Project {TARGET_PROJECT_ID} title is {project.title!r}, expected {EXPECTED_PROJECT_TITLE!r}")

    backend = ensure_backend(project)
    clear_old_predictions(project.id)
    regenerate_for_project(project, backend)
    log(f"Project {TARGET_PROJECT_ID} pose preannotations regenerated")


if __name__ == "__main__":
    main()
