import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

import requests


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/root/label_studio"))
DATA_DIR = Path(os.getenv("BASE_DATA_DIR", str(PROJECT_ROOT / "label_studio_data")))
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "https://ai.hzzootech.com")
PROJECT_TITLE = os.getenv("DOG_POSE_PROJECT_TITLE", "dog_pose_24kpt_preannotation")
ML_URL = os.getenv("DOG_POSE_ML_URL", "http://127.0.0.1:9094")
MODEL_VERSION = os.getenv("DOG_POSE_MODEL_VERSION", "dog_pose_yolo26m_best")

KEYPOINT_LABELS = [
    "front_left_paw",
    "front_left_knee",
    "front_left_elbow",
    "rear_left_paw",
    "rear_left_knee",
    "rear_left_elbow",
    "front_right_paw",
    "front_right_knee",
    "front_right_elbow",
    "rear_right_paw",
    "rear_right_knee",
    "rear_right_elbow",
    "tail_start",
    "tail_end",
    "left_ear_base",
    "right_ear_base",
    "nose",
    "chin",
    "left_ear_tip",
    "right_ear_tip",
    "left_eye",
    "right_eye",
    "withers",
    "throat",
]

COLORS = [
    "#E53935", "#D81B60", "#8E24AA", "#5E35B1", "#3949AB", "#1E88E5",
    "#039BE5", "#00ACC1", "#00897B", "#43A047", "#7CB342", "#C0CA33",
    "#FDD835", "#FFB300", "#FB8C00", "#F4511E", "#6D4C41", "#546E7A",
    "#EC407A", "#7E57C2", "#42A5F5", "#26A69A", "#9CCC65", "#FFA726",
]


def build_label_config():
    labels = "\n".join(
        f'    <Label value="{escape(label)}" background="{color}"/>'
        for label, color in zip(KEYPOINT_LABELS, COLORS)
    )
    return f"""
<View>
  <Header value="YOLO dog pose: one dog box and 24 ordered keypoints"/>
  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="true"/>
  <RectangleLabels name="bbox" toName="image">
    <Label value="dog" background="#1F77B4"/>
  </RectangleLabels>
  <KeyPointLabels name="pose_keypoints" toName="image" strokeWidth="2" pointSize="medium" opacity="0.95">
{labels}
  </KeyPointLabels>
  <View visibleWhen="region-selected">
    <Choices name="visibility" toName="image" perRegion="true" choice="single" required="true" showInline="true">
      <Choice value="1" html="1 occluded but inferable"/>
      <Choice value="2" html="2 visible" selected="true"/>
    </Choices>
  </View>
</View>
""".strip()


LABEL_CONFIG = build_label_config()
EXPERT_INSTRUCTION = """
This project uses a single-class YOLO pose model (dog) with 24 keypoints.
The keypoint order and names match the metadata embedded in dog_pose_yolo26m_best.pt.
Visibility follows the YOLO convention: omit an unknown point, use 1 when occluded but
inferable, and use 2 when visible. Left/right are the dog's anatomical left/right.
The preannotation backend keeps only the highest-confidence dog in each image.
""".strip()


def configure_environment():
    import label_studio

    sys.path.insert(0, str(Path(label_studio.__file__).resolve().parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["HOST"] = PUBLIC_HOST
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.is_file():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_dog_pose_project_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def check_backend():
    response = requests.get(f"{ML_URL}/health", timeout=60)
    response.raise_for_status()
    health = response.json()
    if health.get("status") != "UP":
        raise RuntimeError(f"ML backend is not UP: {health}")
    if health.get("task") != "pose":
        raise RuntimeError(f"Expected pose model, got: {health}")
    if health.get("model_version") != MODEL_VERSION:
        raise RuntimeError(f"Unexpected model version: {health}")
    if health.get("kpt_shape") != [24, 3]:
        raise RuntimeError(f"Expected 24x3 keypoints, got: {health}")
    if health.get("labels") not in ({"0": "dog"}, {0: "dog"}):
        raise RuntimeError(f"Expected one dog class, got: {health}")
    return health


def main():
    health = check_backend()
    configure_environment()

    import django

    django.setup()

    from django.db import transaction
    from ml.models import MLBackend, MLBackendState
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

    existing = Project.objects.filter(title=PROJECT_TITLE).first()
    if existing is not None and Task.objects.filter(project=existing).exists():
        raise RuntimeError(
            f"Project {PROJECT_TITLE!r} already has tasks; refusing to change its configuration"
        )

    backup_path = backup_database()
    with transaction.atomic():
        if existing is None:
            project = Project.objects.create(
                title=PROJECT_TITLE,
                description="Docker-backed YOLO dog pose preannotation (24 keypoints).",
                organization=organization,
                created_by=user,
                label_config=LABEL_CONFIG,
                expert_instruction=EXPERT_INSTRUCTION,
                show_instruction=True,
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
        else:
            project = existing
            project.label_config = LABEL_CONFIG
            project.expert_instruction = EXPERT_INSTRUCTION
            project.show_instruction = True
            project.model_version = MODEL_VERSION
            project.show_collab_predictions = True
            project.save()

        project.validate_label_config(LABEL_CONFIG)
        ProjectMember.objects.get_or_create(user=user, project=project)
        ProjectSummary.objects.get_or_create(project=project)
        backend, created = MLBackend.objects.update_or_create(
            project=project,
            url=ML_URL,
            defaults={
                "title": MODEL_VERSION,
                "model_version": MODEL_VERSION,
                "state": MLBackendState.CONNECTED,
                "error_message": None,
                "is_interactive": False,
                "timeout": 300.0,
                "auto_update": False,
                "auth_method": "NONE",
                "basic_auth_user": "",
                "basic_auth_pass": "",
                "description": "Docker YOLO dog pose preannotation backend",
                "extra_params": {},
            },
        )

    setup_response = requests.post(
        f"{ML_URL}/setup",
        json={"project": project.id, "schema": LABEL_CONFIG, "hostname": PUBLIC_HOST},
        timeout=60,
    )
    setup_response.raise_for_status()

    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"project_title={project.title}")
    print(f"backend_id={backend.id}")
    print(f"backend_created={created}")
    print(f"backend_url={backend.url}")
    print(f"model_version={health['model_version']}")
    print(f"kpt_shape={health['kpt_shape']}")


if __name__ == "__main__":
    main()
