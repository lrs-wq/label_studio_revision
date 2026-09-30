import csv
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
REPORT_DIR = DATA_DIR / "reports"

SOURCE_DIR = Path(r"D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls")
PROJECT_ID = 7
AREA_THRESHOLD = 128 * 128
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def configure_label_studio_environment():
    site_label_studio = ENV_ROOT / "Lib" / "site-packages" / "label_studio"
    sys.path.insert(0, str(site_label_studio))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.exists():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database(timestamp):
    backup_path = DATA_DIR / f"label_studio.before_remove_fur_color_area_lt_128x128_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path


def iter_images():
    yield from sorted(
        path
        for path in SOURCE_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def scan_small_images():
    rows = []
    for path in iter_images():
        with Image.open(path) as image:
            width, height = image.size
        area = width * height
        if area < AREA_THRESHOLD:
            rows.append(
                {
                    "class": path.parent.name,
                    "width": width,
                    "height": height,
                    "area": area,
                    "source_path": str(path),
                    "relative_path": str(path.relative_to(SOURCE_DIR)),
                    "original_split": path.name.split("__", 1)[0] if "__" in path.name else "",
                }
            )
    return rows


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def backup_metadata_files(quarantine_dir):
    metadata_dir = quarantine_dir / "_metadata_before_removal"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "manifest.json",
        "flattened_review_manifest.json",
        "fur_color_summary.json",
        "flattened_review_summary.json",
    ):
        path = SOURCE_DIR / name
        if path.exists():
            shutil.copy2(path, metadata_dir / name)


def load_json_if_exists(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_dataset_metadata(removed_paths):
    for name in ("manifest.json", "flattened_review_manifest.json"):
        path = SOURCE_DIR / name
        data = load_json_if_exists(path)
        if isinstance(data, list):
            filtered = [
                item
                for item in data
                if item.get("file") not in removed_paths and Path(item.get("file", "")).exists()
            ]
            dump_json(path, filtered)

    remaining_counts = Counter()
    remaining_by_split = defaultdict(Counter)
    for image in iter_images():
        cls = image.parent.name
        split = image.name.split("__", 1)[0] if "__" in image.name else ""
        remaining_counts[cls] += 1
        if split:
            remaining_by_split[cls][split] += 1

    for name in ("fur_color_summary.json", "flattened_review_summary.json"):
        path = SOURCE_DIR / name
        data = load_json_if_exists(path)
        if isinstance(data, dict):
            data["counts"] = dict(sorted(remaining_counts.items()))
            data["counts_by_original_split"] = {
                cls: dict(sorted(splits.items())) for cls, splits in sorted(remaining_by_split.items())
            }
            data["total"] = sum(remaining_counts.values())
            data["removed_area_lt_128x128"] = len(removed_paths)
            dump_json(path, data)


def move_source_files(rows, quarantine_dir):
    for row in rows:
        source = Path(row["source_path"])
        destination = quarantine_dir / row["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(destination)
        shutil.move(str(source), str(destination))
        row["quarantine_path"] = str(destination)


def delete_upload_files(upload_files):
    upload_root = (DATA_DIR / "media" / "upload").resolve()
    deleted = 0
    failed = []
    for file_name in upload_files:
        if not file_name:
            continue
        upload_path = (DATA_DIR / "media" / file_name).resolve()
        if upload_path.exists() and upload_path.is_relative_to(upload_root):
            try:
                upload_path.unlink()
                deleted += 1
            except PermissionError:
                failed.append(str(upload_path))
    return deleted, failed


def latest_removal_report():
    reports = sorted(
        REPORT_DIR.glob("fur_color_removed_area_lt_128x128_*.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return reports[0] if reports else None


def removed_paths_from_report(path):
    if not path or not path.exists():
        return set()
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return {row["source_path"] for row in csv.DictReader(file) if row.get("source_path")}


def delete_orphan_upload_files(project_id):
    from data_import.models import FileUpload

    upload_dir = DATA_DIR / "media" / "upload" / str(project_id)
    if not upload_dir.exists():
        return 0, []

    live_files = set(FileUpload.objects.filter(project_id=project_id).values_list("file", flat=True))
    orphan_files = [
        path for path in upload_dir.iterdir()
        if path.is_file() and f"upload/{project_id}/{path.name}" not in live_files
    ]
    return delete_upload_files([f"upload/{project_id}/{path.name}" for path in orphan_files])


def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    quarantine_dir = SOURCE_DIR.parent / f"{SOURCE_DIR.name}_removed_area_lt_128x128_{timestamp}"

    rows = scan_small_images()
    if not rows:
        configure_label_studio_environment()

        import django

        django.setup()

        latest_report = latest_removal_report()
        removed_paths = removed_paths_from_report(latest_report)
        update_dataset_metadata(removed_paths)
        deleted_orphans, failed_orphans = delete_orphan_upload_files(PROJECT_ID)
        print("removed=0")
        print("reason=no images with area < 128*128")
        print(f"metadata_refreshed_from={latest_report or ''}")
        print(f"deleted_orphan_upload_files={deleted_orphans}")
        print(f"failed_orphan_upload_files={len(failed_orphans)}")
        return

    configure_label_studio_environment()

    import django

    django.setup()

    from data_import.models import FileUpload
    from django.db import transaction
    from projects.models import Project
    from tasks.models import Task

    source_paths = {row["source_path"] for row in rows}
    backup_path = backup_database(timestamp)

    project = Project.objects.get(id=PROJECT_ID)
    tasks = list(
        Task.objects.filter(project=project, meta__source_path__in=source_paths)
        .order_by("inner_id", "id")
        .values("id", "inner_id", "data", "meta", "file_upload_id", "total_annotations", "total_predictions")
    )
    task_ids = [task["id"] for task in tasks]
    file_upload_ids = [task["file_upload_id"] for task in tasks if task["file_upload_id"]]
    upload_files = [
        task["meta"].get("upload_file")
        for task in tasks
        if isinstance(task["meta"], dict) and task["meta"].get("upload_file")
    ]
    annotated_tasks = [task for task in tasks if task["total_annotations"]]

    row_by_source = {row["source_path"]: row for row in rows}
    for task in tasks:
        source_path = task["meta"].get("source_path") if isinstance(task["meta"], dict) else None
        if source_path in row_by_source:
            row_by_source[source_path]["task_id"] = task["id"]
            row_by_source[source_path]["inner_id"] = task["inner_id"]
            row_by_source[source_path]["file_upload_id"] = task["file_upload_id"]
            row_by_source[source_path]["total_annotations"] = task["total_annotations"]
            row_by_source[source_path]["total_predictions"] = task["total_predictions"]

    removal_report = REPORT_DIR / f"fur_color_removed_area_lt_128x128_{timestamp}.csv"
    write_csv(
        removal_report,
        rows,
        [
            "class",
            "width",
            "height",
            "area",
            "source_path",
            "relative_path",
            "quarantine_path",
            "original_split",
            "task_id",
            "inner_id",
            "file_upload_id",
            "total_annotations",
            "total_predictions",
        ],
    )

    annotated_report = REPORT_DIR / f"fur_color_removed_area_lt_128x128_annotations_{timestamp}.json"
    annotated_payload = []
    if annotated_tasks:
        for task in Task.objects.filter(id__in=[task["id"] for task in annotated_tasks]).prefetch_related("annotations"):
            annotated_payload.append(
                {
                    "task_id": task.id,
                    "inner_id": task.inner_id,
                    "data": task.data,
                    "meta": task.meta,
                    "annotations": [
                        {
                            "id": annotation.id,
                            "result": annotation.result,
                            "completed_by_id": annotation.completed_by_id,
                            "created_at": annotation.created_at.isoformat(),
                            "updated_at": annotation.updated_at.isoformat(),
                        }
                        for annotation in task.annotations.all()
                    ],
                }
            )
        dump_json(annotated_report, annotated_payload)

    backup_metadata_files(quarantine_dir)
    move_source_files(rows, quarantine_dir)
    write_csv(
        removal_report,
        rows,
        [
            "class",
            "width",
            "height",
            "area",
            "source_path",
            "relative_path",
            "quarantine_path",
            "original_split",
            "task_id",
            "inner_id",
            "file_upload_id",
            "total_annotations",
            "total_predictions",
        ],
    )

    with transaction.atomic():
        Task.delete_tasks_without_signals(Task.objects.filter(id__in=task_ids))
        FileUpload.objects.filter(id__in=file_upload_ids).delete()

        remaining_tasks = list(Task.objects.filter(project=project).order_by("inner_id", "id"))
        for index, task in enumerate(remaining_tasks, start=1):
            task.inner_id = index
        Task.objects.bulk_update(remaining_tasks, ["inner_id"], batch_size=500)

        project.summary.reset(tasks_data_based=True)
        project.summary.update_data_columns(Task.objects.filter(project=project).only("data"))
        project.update_tasks_states(
            maximum_annotations_changed=False,
            overlap_cohort_percentage_changed=False,
            tasks_number_changed=True,
        )

    deleted_upload_files, failed_upload_files = delete_upload_files(upload_files)
    update_dataset_metadata(source_paths)

    remaining_counts = Counter(path.parent.name for path in iter_images())
    print(f"backup={backup_path}")
    print(f"quarantine_dir={quarantine_dir}")
    print(f"removal_report={removal_report}")
    print(f"annotated_report={annotated_report if annotated_tasks else ''}")
    print(f"removed_source_images={len(rows)}")
    print(f"removed_label_studio_tasks={len(task_ids)}")
    print(f"removed_annotated_tasks={len(annotated_tasks)}")
    print(f"deleted_upload_files={deleted_upload_files}")
    print(f"failed_upload_files={len(failed_upload_files)}")
    print(f"remaining_images={sum(remaining_counts.values())}")
    print(f"remaining_counts={dict(sorted(remaining_counts.items()))}")


if __name__ == "__main__":
    main()
