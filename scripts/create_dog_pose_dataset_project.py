from __future__ import annotations

import csv
import json
import os
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

import requests


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/root/label_studio"))
DATA_DIR = Path(os.getenv("BASE_DATA_DIR", str(PROJECT_ROOT / "label_studio_data")))
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
SOURCE_DIR = Path(
    os.getenv("DOG_POSE_SOURCE_DIR", "/data/Eating_behavior/frames_every_50_all")
)
MAPPING_CSV = SOURCE_DIR / "frame_mapping.csv"
PROJECT_TITLE = os.getenv("DOG_POSE_PROJECT_TITLE", "dog_pose_frames_every_50_all")
REFERENCE_PROJECT_TITLE = os.getenv(
    "DOG_POSE_REFERENCE_PROJECT_TITLE", "cat_pose_annotation_seed_800"
)
ML_URL = os.getenv("DOG_POSE_ML_URL", "http://127.0.0.1:9094")
MODEL_VERSION = os.getenv("DOG_POSE_MODEL_VERSION", "dog_pose_yolo26m_best")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "https://ai.hzzootech.com")
BATCH_SIZE = int(os.getenv("DOG_POSE_IMPORT_BATCH_SIZE", "500"))
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# This order must match the kpt_names metadata stored in dog_pose_yolo26m_best.pt.
MODEL_KEYPOINTS = [
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

# Output profile requested for annotation. The first value is the source point
# in the 24-point model and the second is the exact Label Studio label from
# cat_pose_annotation_seed_800.
KEYPOINTS = [
    ("nose", "鼻尖"),
    ("left_eye", "左眼"),
    ("right_eye", "右眼"),
    ("withers", "颈背"),
    ("tail_start", "尾根"),
    ("front_left_elbow", "左前肩"),
    ("front_left_knee", "左前肘"),
    ("front_left_paw", "左前爪"),
    ("front_right_elbow", "右前肩"),
    ("front_right_knee", "右前肘"),
    ("front_right_paw", "右前爪"),
    ("rear_left_elbow", "左后髋"),
    ("rear_left_knee", "左后膝"),
    ("rear_left_paw", "左后爪"),
    ("rear_right_elbow", "右后髋"),
    ("rear_right_knee", "右后膝"),
    ("rear_right_paw", "右后爪"),
]

# Reserve z/x for the reference project's visibility choices.
KEYPOINT_HOTKEYS = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "q", "w", "e",
    "r", "t", "a", "s", "d",
]

EXTRA_COLORS = [
    "#EC407A",
    "#7E57C2",
    "#42A5F5",
    "#26A69A",
    "#9CCC65",
    "#FFA726",
    "#8D6E63",
]


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
    backup_path = DATA_DIR / f"label_studio.before_dog_pose_dataset_{timestamp}.sqlite3"
    if backup_path.exists():
        raise FileExistsError(backup_path)
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def iter_images() -> list[Path]:
    return sorted(
        path
        for path in SOURCE_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def load_frame_mapping() -> dict[str, dict[str, str]]:
    if not MAPPING_CSV.is_file():
        return {}

    rows: dict[str, dict[str, str]] = {}
    with MAPPING_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            filename = (row.get("flat_filename") or "").strip()
            if filename:
                rows[filename] = row
    return rows


def reference_style(label_config: str) -> dict[str, object]:
    root = ET.fromstring(label_config)
    keypoint_tag = next((tag for tag in root.iter() if tag.tag == "KeyPointLabels"), None)
    if keypoint_tag is None:
        raise RuntimeError("Reference project has no KeyPointLabels tag")

    labels = [tag for tag in keypoint_tag if tag.tag == "Label"]
    colors = [tag.attrib.get("background", "#1E88E5") for tag in labels]
    colors.extend(EXTRA_COLORS)

    choices_tag = next(
        (
            tag
            for tag in root.iter()
            if tag.tag == "Choices" and tag.attrib.get("perRegion", "").lower() == "true"
        ),
        None,
    )
    choices = []
    if choices_tag is not None:
        for tag in choices_tag:
            if tag.tag == "Choice":
                choices.append(dict(tag.attrib))

    return {
        "stroke_width": keypoint_tag.attrib.get("strokeWidth", "2"),
        "point_size": keypoint_tag.attrib.get("pointSize", "medium"),
        "opacity": keypoint_tag.attrib.get("opacity", "0.95"),
        "colors": colors,
        "choices": choices,
    }


def build_label_config(reference_label_config: str) -> str:
    style = reference_style(reference_label_config)
    colors = style["colors"]

    label_lines = []
    for index, ((_, value), hotkey) in enumerate(zip(KEYPOINTS, KEYPOINT_HOTKEYS)):
        color = colors[index % len(colors)]
        label_lines.append(
            "    <Label "
            f"value={quoteattr(value)} "
            f"background={quoteattr(color)} hotkey={quoteattr(hotkey)}/>"
        )

    choices = style["choices"] or [
        {"value": "1", "html": "1 遮挡但可推断", "hotkey": "z"},
        {"value": "2", "html": "2 可见", "hotkey": "x"},
    ]
    choice_lines = []
    for choice in choices:
        attributes = " ".join(
            f"{name}={quoteattr(str(value))}" for name, value in choice.items()
        )
        choice_lines.append(f"      <Choice {attributes}/>")

    return f"""
<View>
  <Header value="狗目标框 + 17 点姿态（点位参考 cat_pose_annotation_seed_800）"/>
  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="true"/>
  <RectangleLabels name="bbox" toName="image">
    <Label value="dog" alias="狗" showAlias="true" background="#1F77B4"/>
  </RectangleLabels>
  <KeyPointLabels name="pose_keypoints" toName="image" strokeWidth={quoteattr(str(style["stroke_width"]))} pointSize={quoteattr(str(style["point_size"]))} opacity={quoteattr(str(style["opacity"]))}>
{os.linesep.join(label_lines)}
  </KeyPointLabels>
  <View visibleWhen="region-selected">
    <Choices name="visibility" toName="image" perRegion="true" choice="single" required="false" showInline="true">
{os.linesep.join(choice_lines)}
    </Choices>
  </View>
</View>
""".strip()


def expert_instruction() -> str:
    aliases = "、".join(f"{index + 1}.{label}" for index, (_, label) in enumerate(KEYPOINTS))
    return (
        "本项目用于狗目标检测与姿态检测。每只狗使用 dog 矩形框；只标注指定的 17 个姿态点。"
        "左右方向以狗自身解剖方向为准。可见性规则参考 cat_pose_annotation_seed_800："
        "不可确定的点删除，遮挡但可推断选择 1，清晰可见选择 2。\n\n"
        f"17 点顺序：{aliases}"
    )


def link_or_copy(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    try:
        os.link(source, destination)
        return "hardlink"
    except OSError:
        shutil.copy2(source, destination)
        return "copy"


def prepare_task_data(image: Path, mapping: dict[str, dict[str, str]]) -> tuple[dict, dict]:
    row = mapping.get(image.name, {})
    source_relative_path = (row.get("source_relative_path") or image.name).replace("\\", "/")
    parts = source_relative_path.split("/")
    sequence = parts[0] if len(parts) > 1 else ""
    source_video = parts[1] if len(parts) > 2 else ""
    frame_name = Path(source_relative_path).stem
    frame_index = ""
    if frame_name.startswith("frame_"):
        frame_index = frame_name.removeprefix("frame_")

    data = {
        "image": "",
        "filename": image.name,
        "source_relative_path": source_relative_path,
        "sequence": sequence,
        "source_video": source_video,
        "frame_index": frame_index,
    }
    meta = {
        "source_path": str(image),
        "source_dataset": str(SOURCE_DIR),
        "source_relative_path": source_relative_path,
        "source_size_bytes": int(row.get("source_size_bytes") or image.stat().st_size),
        "keypoint_order": [label for _, label in KEYPOINTS],
        "model_keypoint_mapping": {label: model_name for model_name, label in KEYPOINTS},
        "reference_project": REFERENCE_PROJECT_TITLE,
    }
    return data, meta


def check_backend() -> dict:
    response = requests.get(f"{ML_URL}/health", timeout=60)
    response.raise_for_status()
    health = response.json()
    if health.get("status") != "UP" or health.get("task") != "pose":
        raise RuntimeError(f"Dog pose backend is not healthy: {health}")
    if health.get("model_version") != MODEL_VERSION:
        raise RuntimeError(f"Unexpected model version: {health}")
    if health.get("kpt_shape") != [24, 3]:
        raise RuntimeError(f"Expected 24x3 keypoints: {health}")
    if health.get("keypoints") != MODEL_KEYPOINTS:
        raise RuntimeError("Backend keypoint order does not match the project configuration")
    return health


def main() -> None:
    if not SOURCE_DIR.is_dir():
        raise FileNotFoundError(SOURCE_DIR)
    images = iter_images()
    if not images:
        raise RuntimeError(f"No images found under {SOURCE_DIR}")
    if len({image.name for image in images}) != len(images):
        raise RuntimeError("Source directory contains duplicate image filenames")

    mapping = load_frame_mapping()
    health = check_backend()
    configure_environment()

    import django

    django.setup()

    from data_import.models import FileUpload
    from data_manager.models import View
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

    reference = Project.objects.get(title=REFERENCE_PROJECT_TITLE)
    label_config = build_label_config(reference.label_config)

    existing = Project.objects.filter(title=PROJECT_TITLE).first()
    if existing is not None:
        raise RuntimeError(
            f"Project {PROJECT_TITLE!r} already exists as ID {existing.id}; refusing to overwrite it"
        )

    backup_path = backup_database()
    upload_method_counts: Counter[str] = Counter()
    upload_dir: Path | None = None

    try:
        with transaction.atomic():
            project = Project.objects.create(
                title=PROJECT_TITLE,
                description=(
                    f"Dog detection and selected 17-keypoint pose project imported from {SOURCE_DIR}. "
                    f"Annotation interaction follows {REFERENCE_PROJECT_TITLE}."
                ),
                organization=organization,
                created_by=user,
                label_config=label_config,
                expert_instruction=expert_instruction(),
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
            project.validate_label_config(label_config)
            ProjectMember.objects.get_or_create(user=user, project=project)
            summary, _ = ProjectSummary.objects.get_or_create(project=project)

            upload_dir = DATA_DIR / "media" / "upload" / str(project.id)
            if upload_dir.exists():
                raise FileExistsError(upload_dir)
            upload_dir.mkdir(parents=True)

            prepared = []
            file_uploads = []
            for image in images:
                destination = upload_dir / image.name
                upload_method_counts[link_or_copy(image, destination)] += 1
                file_value = f"upload/{project.id}/{image.name}"
                data, meta = prepare_task_data(image, mapping)
                data["image"] = f"/data/upload/{project.id}/{image.name}"
                prepared.append((image, file_value, data, meta))
                file_uploads.append(FileUpload(user=user, project=project, file=file_value))

            FileUpload.objects.bulk_create(file_uploads, batch_size=BATCH_SIZE)
            upload_by_file = {
                upload.file: upload.id
                for upload in FileUpload.objects.filter(project=project).only("id", "file")
            }

            tasks = []
            for inner_id, (_, file_value, data, meta) in enumerate(prepared, start=1):
                tasks.append(
                    Task(
                        project=project,
                        data=data,
                        meta=meta,
                        file_upload_id=upload_by_file[file_value],
                        is_labeled=False,
                        allow_skip=True,
                        overlap=project.maximum_annotations,
                        inner_id=inner_id,
                        total_annotations=0,
                        cancelled_annotations=0,
                        total_predictions=0,
                    )
                )
            db_tasks = Task.objects.bulk_create(tasks, batch_size=BATCH_SIZE)
            summary.update_data_columns(db_tasks)

            View.objects.create(
                project=project,
                user=user,
                data={
                    "title": "Default",
                    "columns": [
                        "image",
                        "filename",
                        "source_relative_path",
                        "sequence",
                        "source_video",
                        "frame_index",
                    ],
                    "type": "list",
                },
                ordering=[],
                selected_items={},
            )

            backend = MLBackend.objects.create(
                project=project,
                url=ML_URL,
                title=MODEL_VERSION,
                model_version=MODEL_VERSION,
                state=MLBackendState.CONNECTED,
                error_message=None,
                is_interactive=False,
                timeout=600.0,
                auto_update=False,
                auth_method="NONE",
                basic_auth_user="",
                basic_auth_pass="",
                description="Docker YOLO dog detection with selected 17-keypoint pose output",
                extra_params={},
            )
    except Exception:
        if upload_dir is not None and upload_dir.is_dir():
            shutil.rmtree(upload_dir)
        raise

    setup_response = requests.post(
        f"{ML_URL}/setup",
        json={"project": project.id, "schema": label_config, "hostname": PUBLIC_HOST},
        timeout=60,
    )
    setup_response.raise_for_status()

    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"project_title={project.title}")
    print(f"source_dir={SOURCE_DIR}")
    print(f"tasks_imported={len(db_tasks)}")
    print(f"file_uploads={len(file_uploads)}")
    print(f"upload_methods={json.dumps(dict(upload_method_counts), ensure_ascii=False)}")
    print(f"mapping_rows={len(mapping)}")
    print(f"reference_project_id={reference.id}")
    print(f"backend_id={backend.id}")
    print(f"backend_url={backend.url}")
    print(f"model_version={health['model_version']}")
    print(f"keypoint_count={len(KEYPOINTS)}")


if __name__ == "__main__":
    main()
