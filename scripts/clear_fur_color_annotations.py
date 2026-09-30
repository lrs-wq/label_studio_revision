import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
REPORTS_DIR = DATA_DIR / "reports"
PROJECT_ID = 7


def configure_label_studio_environment():
    site_label_studio = ENV_ROOT / "Lib" / "site-packages" / "label_studio"
    sys.path.insert(0, str(site_label_studio))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.exists():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_clear_fur_color_annotations_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path, timestamp


def serialize_datetime(value):
    return value.isoformat() if value else None


def export_annotations(annotations, timestamp):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"fur_color_cleared_annotations_{timestamp}.json"
    records = []
    for annotation in annotations:
        task = annotation.task
        records.append(
            {
                "annotation_id": annotation.id,
                "task_id": annotation.task_id,
                "task_inner_id": task.inner_id if task else None,
                "image": (task.data or {}).get("image") if task else None,
                "result": annotation.result,
                "was_cancelled": annotation.was_cancelled,
                "ground_truth": annotation.ground_truth,
                "completed_by_id": annotation.completed_by_id,
                "updated_by_id": annotation.updated_by_id,
                "created_at": serialize_datetime(annotation.created_at),
                "updated_at": serialize_datetime(annotation.updated_at),
            }
        )
    report_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def main():
    configure_label_studio_environment()

    import django

    django.setup()

    from django.db import transaction
    from django.db.models import Q
    from projects.models import Project
    from tasks.functions import update_tasks_counters
    from tasks.models import Annotation, AnnotationDraft, Task

    project = Project.objects.get(id=PROJECT_ID)
    task_qs = Task.objects.filter(project=project)
    annotations = list(
        Annotation.objects.filter(project=project)
        .select_related("task")
        .order_by("id")
    )
    drafts_qs = AnnotationDraft.objects.filter(Q(task__project=project) | Q(annotation__project=project))

    backup_path, timestamp = backup_database()
    report_path = export_annotations(annotations, timestamp)

    with transaction.atomic():
        drafts_deleted = drafts_qs.delete()[0]
        for annotation in annotations:
            annotation.delete()
        task_qs.order_by("id").update(total_annotations=0, cancelled_annotations=0, is_labeled=False)

    counters_updated = update_tasks_counters(Task.objects.filter(project=project), from_scratch=True)
    Task.objects.filter(project=project).order_by("id").update(is_labeled=False)

    print(f"backup={backup_path}")
    print(f"report={report_path}")
    print(f"annotations_deleted={len(annotations)}")
    print(f"drafts_deleted={drafts_deleted}")
    print(f"counters_updated={counters_updated}")


if __name__ == "__main__":
    main()
