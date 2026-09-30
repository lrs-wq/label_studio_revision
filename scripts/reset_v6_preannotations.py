import json
import os
import sys
import time
from pathlib import Path

import requests


LABEL_STUDIO_SITE_PACKAGES = r"D:\Anaconda3\envs\label-studio\Lib\site-packages\label_studio"
BASE_DATA_DIR = r"D:\gogelila\label_studio\label_studio_data"
ML_URL = "http://127.0.0.1:9090"
MODEL_VERSION = "cat_feeding_head_v6_yolo26s_tune2_200e_b32_wandb"
PREDICT_BATCH_SIZE = int(os.environ.get("PREDICT_BATCH_SIZE", "8"))
HTTP_TIMEOUT = int(os.environ.get("PREDICT_HTTP_TIMEOUT", "120"))


sys.path.insert(0, LABEL_STUDIO_SITE_PACKAGES)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
os.environ["BASE_DATA_DIR"] = BASE_DATA_DIR
os.environ["HOST"] = "http://127.0.0.1:8090"
os.environ["LATEST_VERSION_CHECK"] = "false"

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


def healthcheck():
    response = requests.get(f"{ML_URL}/health", timeout=30)
    response.raise_for_status()
    data = response.json()
    log(f"ML health: model_version={data.get('model_version')} path={data.get('model_path')} labels={data.get('labels')}")
    if data.get("model_version") != MODEL_VERSION:
        raise RuntimeError(f"ML backend is not using {MODEL_VERSION}: {data}")


def target_backend_rows():
    rows = list(MLBackend.objects.select_related("project").filter(url=ML_URL).order_by("project_id", "id"))
    if not rows:
        raise RuntimeError(f"No Label Studio ML backends found for {ML_URL}")
    return rows


def sync_model_versions(backends):
    project_ids = sorted({backend.project_id for backend in backends})

    def update():
        MLBackend.objects.filter(id__in=[backend.id for backend in backends]).update(
            title=MODEL_VERSION,
            model_version=MODEL_VERSION,
            state=MLBackendState.CONNECTED,
            error_message=None,
            auto_update=True,
        )
        Project.objects.filter(id__in=project_ids).update(
            model_version=MODEL_VERSION,
            show_collab_predictions=True,
        )

    retry("sync model versions", update)
    log(f"Synchronized model version for projects={project_ids} and ml_backends={[backend.id for backend in backends]}")


def clear_old_predictions(project_ids):
    before = {
        project_id: Prediction.objects.filter(project_id=project_id).count()
        for project_id in project_ids
    }
    log(f"Old prediction rows before reset: {before}")

    placeholders = ",".join(["%s"] * len(project_ids))

    def delete_sql():
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM prediction_meta WHERE prediction_id IN "
                    f"(SELECT id FROM prediction WHERE project_id IN ({placeholders}))",
                    project_ids,
                )
                cursor.execute(
                    f"UPDATE task_completion SET parent_prediction_id = NULL "
                    f"WHERE parent_prediction_id IN "
                    f"(SELECT id FROM prediction WHERE project_id IN ({placeholders}))",
                    project_ids,
                )
                cursor.execute(
                    f"DELETE FROM prediction WHERE project_id IN ({placeholders})",
                    project_ids,
                )
                cursor.execute(
                    f"UPDATE task SET total_predictions = 0 WHERE project_id IN ({placeholders})",
                    project_ids,
                )

    retry("clear old predictions", delete_sql)
    after = {project_id: Prediction.objects.filter(project_id=project_id).count() for project_id in project_ids}
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

    def bulk_insert():
        Prediction.objects.bulk_create(prediction_objects, batch_size=500)

    retry(f"insert predictions project={project.id}", bulk_insert)
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
        log(f"Batch failed for project={project.id}, size={len(task_batch)}; splitting. Error: {exc}")
        return predict_batch_with_split(project, backend, task_batch[:midpoint]) + predict_batch_with_split(
            project,
            backend,
            task_batch[midpoint:],
        )


def regenerate_for_project(project, backend):
    task_ids = list(Task.objects.filter(project_id=project.id).order_by("id").values_list("id", flat=True))
    total = len(task_ids)
    log(f"Project {project.id} {project.title}: regenerating predictions for {total} tasks")
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
    healthcheck()
    backends = target_backend_rows()
    project_ids = sorted({backend.project_id for backend in backends})
    log(
        "Targets: "
        + ", ".join(
            f"project={backend.project_id} backend={backend.id} title={backend.title!r}"
            for backend in backends
        )
    )
    sync_model_versions(backends)
    clear_old_predictions(project_ids)

    backend_by_project = {backend.project_id: MLBackend.objects.get(pk=backend.id) for backend in backends}
    for project_id in project_ids:
        project = Project.objects.get(pk=project_id)
        regenerate_for_project(project, backend_by_project[project_id])

    log("All v6 preannotations regenerated")


if __name__ == "__main__":
    main()
