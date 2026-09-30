import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(r"D:\gogelila\label_studio")
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
IMAGE_DIR = Path(r"D:\gogelila\Eating_behavior\outputs\training_frames_4000_20260602_05_06\images")
LOCAL_FILES_DOCUMENT_ROOT = IMAGE_DIR.parent
PROJECT_TITLE = "cat_feed_260602"
BATCH_SIZE = 500

LABEL_CONFIG = """
<View>
  <Image name="image" value="$image"/>
  <RectangleLabels name="label" toName="image">
    <Label value="cat_head" background="#1F77B4"/>
    <Label value="feeding_head" background="#D62728"/>
  </RectangleLabels>
</View>
""".strip()


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
    os.environ["LOCAL_FILES_DOCUMENT_ROOT"] = str(LOCAL_FILES_DOCUMENT_ROOT)

    secret_key_file = DATA_DIR / "secret_key.txt"
    if secret_key_file.exists():
        os.environ["SECRET_KEY"] = secret_key_file.read_text(encoding="utf-8").strip()


def iter_images():
    extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    yield from sorted(path for path in IMAGE_DIR.iterdir() if path.is_file() and path.suffix.lower() in extensions)


def main():
    if not IMAGE_DIR.exists():
        raise FileNotFoundError(IMAGE_DIR)

    configure_label_studio_environment()

    import django

    django.setup()

    from data_manager.models import View
    from django.db import transaction
    from io_storages.localfiles.models import LocalFilesImportStorage
    from organizations.models import Organization
    from projects.models import Project
    from tasks.models import Task
    from users.models import User

    images = list(iter_images())
    if not images:
        raise RuntimeError(f"No images found in {IMAGE_DIR}")

    user = User.objects.filter(is_active=True).order_by("id").first()
    if user is None:
        raise RuntimeError("No active Label Studio user found")

    organization = Organization.objects.filter(created_by=user).order_by("id").first()
    if organization is None:
        organization = Organization.objects.order_by("id").first()
    if organization is None:
        raise RuntimeError("No Label Studio organization found")

    with transaction.atomic():
        project = Project.objects.filter(title=PROJECT_TITLE, deleted_at__isnull=True).first()
        if project is None:
            project = Project(
                title=PROJECT_TITLE,
                organization=organization,
                created_by=user,
                label_config=LABEL_CONFIG,
                is_published=True,
                is_draft=False,
                maximum_annotations=1,
            )
            project.save()
            created = True
        else:
            project.organization = project.organization or organization
            project.created_by = project.created_by or user
            project.label_config = LABEL_CONFIG
            project.is_published = True
            project.is_draft = False
            project.maximum_annotations = 1
            project.save()
            created = False

        storage, _ = LocalFilesImportStorage.objects.update_or_create(
            project=project,
            path=str(IMAGE_DIR),
            defaults={
                "title": "cat_feed_260602_images",
                "regex_filter": r".*\.jpg$",
                "use_blob_urls": True,
                "recursive_scan": False,
                "synchronizable": False,
            },
        )
        storage.validate_connection()

        existing_tasks = Task.objects.filter(project=project).count()
        if existing_tasks and existing_tasks != len(images):
            raise RuntimeError(
                f"Project {PROJECT_TITLE!r} already has {existing_tasks} tasks, "
                f"but {len(images)} images were found."
            )

        relative_urls = {
            image.name: f"/data/local-files/?d={(image.relative_to(LOCAL_FILES_DOCUMENT_ROOT)).as_posix()}"
            for image in images
        }

        if existing_tasks:
            tasks_to_update = []
            for task in Task.objects.filter(project=project).order_by("inner_id"):
                filename = task.data.get("filename") or task.meta.get("filename")
                if filename not in relative_urls:
                    raise RuntimeError(f"Task {task.id} has unknown filename: {filename!r}")

                task.data = {"image": relative_urls[filename], "filename": filename}
                task.meta = {
                    "filename": filename,
                    "source_path": str(IMAGE_DIR / filename),
                }
                tasks_to_update.append(task)

            Task.objects.bulk_update(tasks_to_update, ["data", "meta"], batch_size=BATCH_SIZE)
            created_tasks = tasks_to_update
        else:
            tasks = [
                Task(
                    project=project,
                    data={"image": relative_urls[image.name], "filename": image.name},
                    meta={"filename": image.name, "source_path": str(image)},
                    overlap=project.maximum_annotations,
                    allow_skip=True,
                    inner_id=index,
                )
                for index, image in enumerate(images, start=1)
            ]

            created_tasks = Task.objects.bulk_create(tasks, batch_size=BATCH_SIZE)

        task_queryset = Task.objects.filter(project=project)
        project.update_tasks_counters_and_task_states(
            tasks_queryset=task_queryset,
            maximum_annotations_changed=False,
            overlap_cohort_percentage_changed=False,
            tasks_number_changed=True,
            recalculate_stats_counts={"task_count": len(created_tasks), "annotation_count": 0, "prediction_count": 0},
        )
        project.summary.update_data_columns(task_queryset.only("data"))

        View.objects.get_or_create(
            project=project,
            user=user,
            defaults={
                "data": {
                    "title": "Default",
                    "columns": ["image", "filename"],
                    "type": "list",
                },
                "ordering": [],
                "selected_items": {},
            },
        )

    print(f"project_created={created}")
    print(f"project_id={project.id}")
    print(f"project_title={project.title}")
    print(f"tasks_imported={len(created_tasks)}")
    print(f"image_dir={IMAGE_DIR}")
    print(f"local_files_document_root={LOCAL_FILES_DOCUMENT_ROOT}")


if __name__ == "__main__":
    main()
