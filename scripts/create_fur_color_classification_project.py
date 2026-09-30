import hashlib
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"

SOURCE_DIR = Path(r"D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls")
TARGET_PROJECT_TITLE = "v9_yolo26_fur_color_cls"
OLD_TEST_PROJECT_TITLE = "cat_behavior_classification"
MODEL_VERSION = "folder_prelabel_v9_yolo26_fur_color_cls"
BATCH_SIZE = 500
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXTRA_DISPLAY_NAMES = {
    "mixed_white_ragdoll": "\u6df7\u767d/\u5e03\u5076",
    "hairless_cat": "\u65e0\u6bdb\u732b",
    "blue_cat": "\u84dd\u732b",
    "silver_gray_white": "\u94f6\u767d/\u7070\u767d",
}
LABEL_ORDER = [
    "mixed_white_ragdoll",
    "white_cream_point",
    "silver_gray_white",
    "gray_brown_tabby",
    "orange_cream",
    "black_dark",
    "black_white",
    "calico_tortie",
    "hairless_cat",
    "blue_cat",
    "mixed_other",
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
    os.environ["LOCAL_FILES_SERVING_ENABLED"] = "true"
    if SECRET_KEY_FILE.exists():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_fur_color_classification_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path


def load_class_names():
    classes_path = SOURCE_DIR / "classes.txt"
    if classes_path.exists():
        labels = [
            line.strip()
            for line in classes_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    else:
        labels = sorted(path.name for path in SOURCE_DIR.iterdir() if path.is_dir())

    if not labels:
        raise RuntimeError(f"No classes found in {SOURCE_DIR}")

    missing_dirs = [label for label in labels if not (SOURCE_DIR / label).is_dir()]
    if missing_dirs:
        raise RuntimeError(f"Classes do not have matching folders: {missing_dirs}")

    return labels


def load_class_display_names(labels):
    display_names = {label: label for label in labels}
    classes_zh_path = SOURCE_DIR / "classes_zh.txt"
    if not classes_zh_path.exists():
        return display_names

    for line in classes_zh_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0] in display_names:
            display_names[parts[0]] = parts[1].strip()

    display_names.update(EXTRA_DISPLAY_NAMES)
    return display_names


def add_extra_labels(labels):
    labels = list(labels)
    for label in EXTRA_DISPLAY_NAMES:
        if label not in labels:
            labels.append(label)
    return order_labels(labels)


def order_labels(labels):
    ordered = [label for label in LABEL_ORDER if label in labels]
    remaining = [label for label in labels if label not in LABEL_ORDER]
    if "mixed_other" in ordered:
        ordered = [label for label in ordered if label != "mixed_other"]
        return ordered + remaining + ["mixed_other"]
    return ordered + remaining


def build_label_config(labels):
    labels = add_extra_labels(labels)
    display_names = load_class_display_names(labels)
    choices = "\n".join(build_choice_xml(index, label, display_names[label]) for index, label in enumerate(labels, start=1))
    return f"""
<View>
  <Image name="image" value="$image"/>
  <Choices name="fur_color" toName="image" choice="single" showInLine="true" required="true">
{choices}
  </Choices>
</View>
""".strip()


def hotkey_for_label(index, label):
    if label == "mixed_other":
        return "0"
    if 1 <= index <= 9:
        return str(index)
    return ""


def build_choice_xml(index, label, display_name):
    hotkey = hotkey_for_label(index, label)
    hotkey_attr = f' hotkey="{hotkey}"' if hotkey else ""
    return (
        f'    <Choice value="{escape(label)}" '
        f'html="{index} {escape(display_name)}" '
        f'hint="{escape(label)}"{hotkey_attr}/>'
    )


def iter_images(labels):
    for label in labels:
        class_dir = SOURCE_DIR / label
        for image in sorted(path for path in class_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS):
            yield label, image


def original_split_from_name(filename):
    return filename.split("__", 1)[0] if "__" in filename else ""


def short_upload_name(index, source_path):
    digest = hashlib.sha1(str(source_path).encode("utf-8")).hexdigest()[:12]
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


def main():
    if not SOURCE_DIR.exists():
        raise FileNotFoundError(SOURCE_DIR)

    labels = load_class_names()
    image_items = list(iter_images(labels))
    if not image_items:
        raise RuntimeError(f"No images found in {SOURCE_DIR}")

    configure_label_studio_environment()

    import django

    django.setup()

    from data_import.models import FileUpload
    from data_manager.models import View
    from django.db import transaction
    from organizations.models import Organization
    from projects.models import Project, ProjectMember, ProjectSummary
    from tasks.models import Prediction, Task
    from users.models import User

    user = User.objects.filter(is_active=True).order_by("id").first()
    if user is None:
        raise RuntimeError("No active Label Studio user found")

    organization = Organization.objects.filter(created_by=user).order_by("id").first()
    if organization is None:
        organization = Organization.objects.order_by("id").first()
    if organization is None:
        raise RuntimeError("No Label Studio organization found")

    backup_path = backup_database()
    label_config = build_label_config(labels)
    deleted_test_projects = 0
    upload_method_counts = Counter()

    with transaction.atomic():
        old_test_projects = list(Project.all_objects.filter(title=OLD_TEST_PROJECT_TITLE).order_by("id"))
        deleted_test_projects = len(old_test_projects)
        old_test_upload_dirs = [DATA_DIR / "media" / "upload" / str(project.id) for project in old_test_projects]
        Project.all_objects.filter(id__in=[project.id for project in old_test_projects]).delete()

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
            project.model_version = MODEL_VERSION
            project.show_collab_predictions = True
            project.enable_empty_annotation = False
            project.maximum_annotations = 1
            project.is_published = True
            project.is_draft = False
            project.save()
        else:
            project = Project.objects.create(
                title=TARGET_PROJECT_TITLE,
                description=(
                    "Fur color image classification project imported from "
                    f"{SOURCE_DIR}. Folder names are used as source prelabels."
                ),
                organization=organization,
                created_by=user,
                label_config=label_config,
                model_version=MODEL_VERSION,
                show_collab_predictions=True,
                enable_empty_annotation=False,
                maximum_annotations=1,
                min_annotations_to_start_training=0,
                overlap_cohort_percentage=100,
                sampling="Sequential sampling",
                is_published=True,
                is_draft=False,
            )

        ProjectMember.objects.get_or_create(user=user, project=project)
        summary, _ = ProjectSummary.objects.get_or_create(project=project)
        summary.reset(tasks_data_based=True)

        upload_dir = DATA_DIR / "media" / "upload" / str(project.id)
        if upload_dir.exists():
            shutil.rmtree(upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        file_uploads = []
        prepared = []
        for index, (label, source_path) in enumerate(image_items, start=1):
            upload_name = short_upload_name(index, source_path)
            upload_path = upload_dir / upload_name
            upload_method_counts[link_or_copy(source_path, upload_path)] += 1

            file_value = f"upload/{project.id}/{upload_name}"
            file_uploads.append(FileUpload(user=user, project=project, file=file_value))
            prepared.append(
                {
                    "label": label,
                    "source_path": source_path,
                    "upload_name": upload_name,
                    "file_value": file_value,
                    "original_split": original_split_from_name(source_path.name),
                }
            )

        FileUpload.objects.bulk_create(file_uploads, batch_size=BATCH_SIZE)
        upload_by_file = {
            upload.file: upload.id
            for upload in FileUpload.objects.filter(project=project, file__in=[item["file_value"] for item in prepared])
        }

        created_tasks = []
        for index, item in enumerate(prepared, start=1):
            image_url = f"/data/upload/{project.id}/{item['upload_name']}"
            task = Task(
                project=project,
                data={
                    "image": image_url,
                    "filename": item["source_path"].name,
                    "fur_color_source": item["label"],
                    "original_split": item["original_split"],
                },
                meta={
                    "source_path": str(item["source_path"]),
                    "source_filename": item["source_path"].name,
                    "source_class": item["label"],
                    "original_split": item["original_split"],
                    "upload_file": item["file_value"],
                },
                file_upload_id=upload_by_file[item["file_value"]],
                is_labeled=False,
                allow_skip=True,
                overlap=project.maximum_annotations,
                inner_id=index,
                total_annotations=0,
                cancelled_annotations=0,
                total_predictions=1,
            )
            created_tasks.append(task)

        db_tasks = Task.objects.bulk_create(created_tasks, batch_size=BATCH_SIZE)
        summary.update_data_columns(db_tasks)

        predictions = []
        for task, item in zip(db_tasks, prepared):
            predictions.append(
                Prediction(
                    task=task,
                    project=project,
                    model_version=MODEL_VERSION,
                    score=1.0,
                    result=[
                        {
                            "from_name": "fur_color",
                            "to_name": "image",
                            "type": "choices",
                            "origin": "prediction",
                            "value": {"choices": [item["label"]]},
                        }
                    ],
                )
            )
        Prediction.objects.bulk_create(predictions, batch_size=BATCH_SIZE)

        View.objects.get_or_create(
            project=project,
            user=user,
            defaults={
                "data": {
                    "title": "Default",
                    "columns": ["image", "filename", "fur_color_source", "original_split"],
                    "type": "list",
                },
                "ordering": [],
                "selected_items": {},
            },
        )

    # Delete old test upload folders after the DB transaction succeeds.
    for upload_dir in old_test_upload_dirs:
        if upload_dir.exists() and upload_dir.resolve().is_relative_to((DATA_DIR / "media" / "upload").resolve()):
            shutil.rmtree(upload_dir)

    label_counts = Counter(label for label, _ in image_items)
    print(f"backup={backup_path}")
    print(f"deleted_test_projects={deleted_test_projects}")
    print(f"project_id={project.id}")
    print(f"title={project.title}")
    print(f"labels={','.join(labels)}")
    print(f"tasks_imported={len(db_tasks)}")
    print(f"predictions_imported={len(predictions)}")
    print(f"upload_methods={dict(upload_method_counts)}")
    print(f"counts={dict(label_counts)}")


if __name__ == "__main__":
    main()
