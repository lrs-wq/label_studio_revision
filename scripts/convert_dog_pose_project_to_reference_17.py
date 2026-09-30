from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/root/label_studio"))
DATA_DIR = Path(os.getenv("BASE_DATA_DIR", str(PROJECT_ROOT / "label_studio_data")))
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
PROJECT_ID = int(os.getenv("DOG_POSE_PROJECT_ID", "22"))
REFERENCE_PROJECT_TITLE = os.getenv(
    "DOG_POSE_REFERENCE_PROJECT_TITLE", "cat_pose_annotation_seed_800"
)
ML_URL = os.getenv("DOG_POSE_ML_URL", "http://127.0.0.1:9094")
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "https://ai.hzzootech.com")
BATCH_SIZE = int(os.getenv("DOG_POSE_CONVERT_BATCH_SIZE", "500"))

# Source names are the embedded 24-point model order. Output labels and order
# exactly match cat_pose_annotation_seed_800.
SELECTED_KEYPOINTS = [
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

MODEL_TO_OUTPUT = dict(SELECTED_KEYPOINTS)
OUTPUT_TO_MODEL = {output: model for model, output in SELECTED_KEYPOINTS}
OUTPUT_LABELS = [output for _, output in SELECTED_KEYPOINTS]
OUTPUT_LABEL_INDEX = {label: index for index, label in enumerate(OUTPUT_LABELS)}


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
    backup_path = DATA_DIR / f"label_studio.before_dog_pose_17kpt_{timestamp}.sqlite3"
    if backup_path.exists():
        raise FileExistsError(backup_path)
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
    return backup_path


def find_tag(root: ET.Element, name: str) -> ET.Element:
    tag = next((candidate for candidate in root.iter() if candidate.tag == name), None)
    if tag is None:
        raise RuntimeError(f"Reference project has no {name} tag")
    return tag


def build_label_config(reference_label_config: str) -> str:
    reference_root = ET.fromstring(reference_label_config)
    image_tag = find_tag(reference_root, "Image")
    keypoint_tag = find_tag(reference_root, "KeyPointLabels")
    choices_tag = find_tag(reference_root, "Choices")

    reference_labels = [
        child.attrib.get("value") for child in keypoint_tag if child.tag == "Label"
    ]
    if reference_labels != OUTPUT_LABELS:
        raise RuntimeError(
            "Reference keypoint order changed: "
            + json.dumps(reference_labels, ensure_ascii=False)
        )

    root = ET.Element("View")
    ET.SubElement(
        root,
        "Header",
        {"value": "狗目标框 + 17 点姿态（点位参考 cat_pose_annotation_seed_800）"},
    )
    root.append(copy.deepcopy(image_tag))
    boxes = ET.SubElement(root, "RectangleLabels", {"name": "bbox", "toName": "image"})
    ET.SubElement(
        boxes,
        "Label",
        {
            "value": "dog",
            "alias": "狗",
            "showAlias": "true",
            "background": "#1F77B4",
        },
    )
    root.append(copy.deepcopy(keypoint_tag))
    selected_view = ET.SubElement(root, "View", {"visibleWhen": "region-selected"})
    selected_view.append(copy.deepcopy(choices_tag))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def convert_prediction_result(regions: list[dict]) -> tuple[list[dict], Counter[str], list[float]]:
    converted_labels_by_id: dict[str, str] = {}
    stats: Counter[str] = Counter()

    for region in regions:
        if region.get("type") != "keypointlabels":
            continue
        labels = (region.get("value") or {}).get("keypointlabels") or []
        if len(labels) != 1:
            raise RuntimeError(f"Malformed keypoint region: {region}")
        old_label = labels[0]
        output_label = MODEL_TO_OUTPUT.get(old_label)
        if output_label is None and old_label in OUTPUT_TO_MODEL:
            output_label = old_label
        if output_label is not None:
            converted_labels_by_id[str(region.get("id"))] = output_label

    converted: list[dict] = []
    scoring_values: list[float] = []
    for region in regions:
        region_type = region.get("type")
        if region_type == "rectanglelabels":
            converted.append(copy.deepcopy(region))
            stats["rectanglelabels"] += 1
            if isinstance(region.get("score"), (int, float)):
                scoring_values.append(float(region["score"]))
            continue

        if region_type == "keypointlabels":
            region_id = str(region.get("id"))
            output_label = converted_labels_by_id.get(region_id)
            if output_label is None:
                stats["dropped_keypointlabels"] += 1
                continue
            converted_region = copy.deepcopy(region)
            converted_region["value"]["keypointlabels"] = [output_label]
            converted.append(converted_region)
            stats["keypointlabels"] += 1
            stats[f"label:{output_label}"] += 1
            if isinstance(region.get("score"), (int, float)):
                scoring_values.append(float(region["score"]))
            continue

        if region_type == "choices":
            if str(region.get("id")) not in converted_labels_by_id:
                stats["dropped_choices"] += 1
                continue
            converted.append(copy.deepcopy(region))
            stats["choices"] += 1
            continue

        raise RuntimeError(f"Unexpected prediction region type: {region_type}")

    def region_sort_key(region: dict) -> tuple[int, int, int]:
        if region.get("type") == "rectanglelabels":
            return (0, 0, 0)
        output_label = converted_labels_by_id[str(region.get("id"))]
        point_before_choice = 0 if region.get("type") == "keypointlabels" else 1
        return (1, OUTPUT_LABEL_INDEX[output_label], point_before_choice)

    converted.sort(key=region_sort_key)
    return converted, stats, scoring_values


def expert_instruction() -> str:
    order = "、".join(f"{index}.{label}" for index, label in enumerate(OUTPUT_LABELS, 1))
    return (
        "本项目用于狗目标检测与 17 点姿态检测。每只狗使用 dog 矩形框。"
        "左右方向以狗自身解剖方向为准。不可确定的点删除；遮挡但可推断选择 1；"
        "清晰可见选择 2。\n\n"
        f"17 点顺序：{order}"
    )


def main() -> None:
    import requests

    configure_environment()
    import django

    django.setup()

    from django.db import transaction
    from django.utils import timezone
    from ml.models import MLBackend, MLBackendState
    from projects.models import Project
    from tasks.functions import update_tasks_counters
    from tasks.models import Annotation, Prediction, Task

    project = Project.objects.get(pk=PROJECT_ID)
    reference = Project.objects.get(title=REFERENCE_PROJECT_TITLE)
    label_config = build_label_config(reference.label_config)
    project.validate_label_config(label_config)

    task_queryset = Task.objects.filter(project=project)
    prediction_queryset = Prediction.objects.filter(project=project).order_by("id")
    task_count = task_queryset.count()
    prediction_count = prediction_queryset.count()
    annotation_count = Annotation.objects.filter(project=project).count()
    prediction_task_count = prediction_queryset.values("task_id").distinct().count()
    if annotation_count:
        raise RuntimeError(
            f"Project {PROJECT_ID} has {annotation_count} annotations; refusing to change labels"
        )
    if prediction_count != task_count or prediction_task_count != task_count:
        raise RuntimeError(
            f"Expected one prediction per task, got tasks={task_count}, "
            f"predictions={prediction_count}, prediction_tasks={prediction_task_count}"
        )

    backup_path = backup_database()
    now = timezone.now()
    total_stats: Counter[str] = Counter()
    prediction_objects = []
    for prediction in prediction_queryset.iterator(chunk_size=BATCH_SIZE):
        converted, stats, scoring_values = convert_prediction_result(prediction.result or [])
        prediction.result = converted
        prediction.score = (
            sum(scoring_values) / len(scoring_values) if scoring_values else 0.0
        )
        prediction.updated_at = now
        prediction_objects.append(prediction)
        total_stats.update(stats)

    task_objects = []
    for task in task_queryset.iterator(chunk_size=BATCH_SIZE):
        meta = dict(task.meta or {})
        meta["keypoint_order"] = OUTPUT_LABELS
        meta["model_keypoint_mapping"] = OUTPUT_TO_MODEL
        meta.pop("keypoint_aliases", None)
        task.meta = meta
        task_objects.append(task)

    with transaction.atomic():
        project.label_config = label_config
        project.description = (
            "Dog detection and selected 17-keypoint pose project imported from "
            "/data/Eating_behavior/frames_every_50_all. Point names, colors, hotkeys, "
            "and visibility choices follow cat_pose_annotation_seed_800."
        )
        project.expert_instruction = expert_instruction()
        project.show_instruction = True
        project.parsed_label_config = None
        project.show_collab_predictions = True
        project.save(
            update_fields=[
                "label_config",
                "description",
                "expert_instruction",
                "show_instruction",
                "parsed_label_config",
                "show_collab_predictions",
            ]
        )
        Prediction.objects.bulk_update(
            prediction_objects,
            ["result", "score", "updated_at"],
            batch_size=BATCH_SIZE,
        )
        Task.objects.bulk_update(task_objects, ["meta"], batch_size=BATCH_SIZE)
        MLBackend.objects.filter(project=project).update(
            description="Docker YOLO dog detection with selected 17-keypoint pose output",
            state=MLBackendState.CONNECTED,
            error_message=None,
        )

    setup_response = requests.post(
        f"{ML_URL}/setup",
        json={"project": project.id, "schema": label_config, "hostname": PUBLIC_HOST},
        timeout=60,
    )
    setup_response.raise_for_status()
    counter_updates = update_tasks_counters(task_queryset, from_scratch=True)

    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"tasks={task_count}")
    print(f"predictions={prediction_count}")
    print(f"annotations={annotation_count}")
    print(f"keypoint_labels={json.dumps(OUTPUT_LABELS, ensure_ascii=False)}")
    print(f"region_stats={json.dumps(dict(total_stats), ensure_ascii=False, sort_keys=True)}")
    print(f"counter_updates={counter_updates}")


if __name__ == "__main__":
    main()
