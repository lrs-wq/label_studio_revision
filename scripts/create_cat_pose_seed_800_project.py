import csv
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"

SOURCE_ROOT = Path(r"D:\gogelila\Eating_behavior\data\cat_pose_annotation_seed_800")
IMAGE_DIR = SOURCE_ROOT / "images"
METADATA_PATH = SOURCE_ROOT / "metadata" / "selected_high_quality_nonduplicate_800.csv"
TARGET_PROJECT_TITLE = "cat_pose_annotation_seed_800"
REFERENCE_PROJECT_ID = 8
MODEL_VERSION = "cat_pose_keypoints_seed_800"
BATCH_SIZE = 500
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

KEYPOINT_ORDER = [
    "\u9f3b\u5c16",
    "\u5de6\u773c",
    "\u53f3\u773c",
    "\u9888\u80cc",
    "\u5c3e\u6839",
    "\u5de6\u524d\u80a9",
    "\u5de6\u524d\u8098",
    "\u5de6\u524d\u722a",
    "\u53f3\u524d\u80a9",
    "\u53f3\u524d\u8098",
    "\u53f3\u524d\u722a",
    "\u5de6\u540e\u9acb",
    "\u5de6\u540e\u819d",
    "\u5de6\u540e\u722a",
    "\u53f3\u540e\u9acb",
    "\u53f3\u540e\u819d",
    "\u53f3\u540e\u722a",
]

TASK_METADATA_FIELDS = [
    "status",
    "reasons",
    "quality_score",
    "width",
    "height",
    "cat_count",
    "det_confidence",
    "x1",
    "y1",
    "x2",
    "y2",
    "box_width",
    "box_height",
    "box_area_ratio",
    "edge_touch_count",
    "blur_var",
    "brightness",
    "contrast",
    "phash",
    "source_sha1",
]


def configure_label_studio_environment():
    site_label_studio = ENV_ROOT / "Lib" / "site-packages" / "label_studio"
    sys.path.insert(0, str(site_label_studio))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["CONDA_ENVS_PATH"] = r"D:\Anaconda3\envs"
    os.environ["CONDA_PKGS_DIRS"] = r"D:\Anaconda3\pkgs"
    os.environ["PIP_CACHE_DIR"] = str(PROJECT_ROOT / "pip_cache")
    os.environ["TEMP"] = str(PROJECT_ROOT / "tmp")
    os.environ["TMP"] = str(PROJECT_ROOT / "tmp")
    os.environ["LOCALAPPDATA"] = str(PROJECT_ROOT / "appdata" / "Local")
    os.environ["APPDATA"] = str(PROJECT_ROOT / "appdata" / "Roaming")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.exists():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_cat_pose_seed_800_import_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path


def iter_images():
    return sorted(
        path
        for path in IMAGE_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def load_metadata():
    if not METADATA_PATH.is_file():
        return {}

    rows = {}
    with METADATA_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            relative_path = (row.get("relative_path") or "").replace("\\", "/")
            if relative_path:
                rows[relative_path] = row
    return rows


def short_upload_name(index, source_path):
    relative = source_path.relative_to(IMAGE_DIR).as_posix()
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
    return f"{index:06d}_{digest}{source_path.suffix.lower()}"


def link_or_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def task_record_for_image(image, metadata_by_relative):
    relative_path = image.relative_to(IMAGE_DIR).as_posix()
    parts = relative_path.split("/")
    split = parts[0] if len(parts) >= 3 else ""
    class_name = parts[1] if len(parts) >= 3 else ""
    metadata = metadata_by_relative.get(relative_path, {})

    data = {
        "image": "",
        "filename": image.name,
        "relative_path": relative_path,
        "split": split,
        "class_name": class_name,
    }

    for field in TASK_METADATA_FIELDS:
        if metadata.get(field) not in (None, ""):
            data[field] = metadata[field]

    return {
        "image": image,
        "relative_path": relative_path,
        "split": split,
        "class_name": class_name,
        "metadata": metadata,
        "data": data,
    }


def main():
    if not IMAGE_DIR.is_dir():
        raise FileNotFoundError(IMAGE_DIR)

    images = list(iter_images())
    if not images:
        raise RuntimeError(f"No images found in {IMAGE_DIR}")

    metadata_by_relative = load_metadata()
    prepared = [task_record_for_image(image, metadata_by_relative) for image in images]
    missing_metadata = [item["relative_path"] for item in prepared if not item["metadata"]]

    configure_label_studio_environment()

    import django

    django.setup()

    from data_import.models import FileUpload
    from data_manager.models import View
    from django.db import transaction
    from organizations.models import Organization
    from projects.models import Project, ProjectMember, ProjectSummary
    from tasks.models import Task
    from users.models import User

    user = User.objects.filter(is_active=True).order_by("id").first()
    if user is None:
        raise RuntimeError("No active Label Studio user found")

    organization = Organization.objects.filter(created_by=user).order_by("id").first()
    if organization is None:
        organization = Organization.objects.order_by("id").first()
    if organization is None:
        raise RuntimeError("No Label Studio organization found")

    reference_project = Project.objects.get(id=REFERENCE_PROJECT_ID)
    label_config = reference_project.label_config
    expert_instruction = reference_project.expert_instruction

    backup_path = backup_database()
    upload_method_counts = Counter()

    with transaction.atomic():
        existing_target = Project.objects.filter(title=TARGET_PROJECT_TITLE).first()
        if existing_target and Task.objects.filter(project=existing_target).exists():
            raise RuntimeError(
                f"Target project {TARGET_PROJECT_TITLE!r} already exists with tasks. "
                "Delete it first if you want to recreate it."
            )

        if existing_target:
            project = existing_target
            project.organization = project.organization or organization
            project.created_by = project.created_by or user
            project.label_config = label_config
            project.expert_instruction = expert_instruction
            project.show_instruction = True
            project.model_version = MODEL_VERSION
            project.show_collab_predictions = False
            project.enable_empty_annotation = False
            project.maximum_annotations = 1
            project.is_published = True
            project.is_draft = False
            project.save()
        else:
            project = Project.objects.create(
                title=TARGET_PROJECT_TITLE,
                description=f"Cat pose keypoint annotation project imported from {SOURCE_ROOT}.",
                organization=organization,
                created_by=user,
                label_config=label_config,
                expert_instruction=expert_instruction,
                show_instruction=True,
                model_version=MODEL_VERSION,
                show_collab_predictions=False,
                enable_empty_annotation=False,
                maximum_annotations=1,
                min_annotations_to_start_training=0,
                overlap_cohort_percentage=100,
                sampling="Sequential sampling",
                is_published=True,
                is_draft=False,
            )

        project.validate_label_config(label_config)
        ProjectMember.objects.get_or_create(user=user, project=project)
        summary, _ = ProjectSummary.objects.get_or_create(project=project)
        summary.reset(tasks_data_based=True)

        upload_dir = DATA_DIR / "media" / "upload" / str(project.id)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        file_uploads = []
        for index, item in enumerate(prepared, start=1):
            upload_name = short_upload_name(index, item["image"])
            upload_path = upload_dir / upload_name
            upload_method_counts[link_or_copy(item["image"], upload_path)] += 1

            file_value = f"upload/{project.id}/{upload_name}"
            item["upload_name"] = upload_name
            item["file_value"] = file_value
            item["data"]["image"] = f"/data/upload/{project.id}/{upload_name}"
            file_uploads.append(FileUpload(user=user, project=project, file=file_value))

        FileUpload.objects.bulk_create(file_uploads, batch_size=BATCH_SIZE)
        upload_by_file = {
            upload.file: upload.id
            for upload in FileUpload.objects.filter(project=project, file__in=[item["file_value"] for item in prepared])
        }

        tasks = []
        for index, item in enumerate(prepared, start=1):
            tasks.append(
                Task(
                    project=project,
                    data=item["data"],
                    meta={
                        "source_path": str(item["image"]),
                        "source_dataset": str(SOURCE_ROOT),
                        "relative_path": item["relative_path"],
                        "split": item["split"],
                        "class_name": item["class_name"],
                        "metadata": item["metadata"],
                        "keypoint_order": KEYPOINT_ORDER,
                    },
                    file_upload_id=upload_by_file[item["file_value"]],
                    is_labeled=False,
                    allow_skip=True,
                    overlap=project.maximum_annotations,
                    inner_id=index,
                    total_annotations=0,
                    cancelled_annotations=0,
                    total_predictions=0,
                )
            )

        db_tasks = Task.objects.bulk_create(tasks, batch_size=BATCH_SIZE)
        summary.update_data_columns(db_tasks)

        View.objects.get_or_create(
            project=project,
            user=user,
            defaults={
                "data": {
                    "title": "Default",
                    "columns": [
                        "image",
                        "filename",
                        "split",
                        "class_name",
                        "quality_score",
                        "det_confidence",
                        "box_area_ratio",
                        "edge_touch_count",
                    ],
                    "type": "list",
                },
                "ordering": [],
                "selected_items": {},
            },
        )

    split_counts = Counter(item["split"] for item in prepared)
    class_counts = Counter(item["class_name"] for item in prepared)
    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"title={project.title}")
    print(f"tasks_imported={len(db_tasks)}")
    print(f"missing_metadata={len(missing_metadata)}")
    print(f"upload_methods={dict(upload_method_counts)}")
    print(f"split_counts={json.dumps(dict(sorted(split_counts.items())), ensure_ascii=False)}")
    print(f"class_counts={json.dumps(dict(sorted(class_counts.items())), ensure_ascii=False)}")
    print(f"keypoints={json.dumps(KEYPOINT_ORDER, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
