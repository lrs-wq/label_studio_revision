import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO


MODEL_PATH = os.getenv("MODEL_PATH", "/models/dog_pose_yolo26m_best.pt")
MODEL_VERSION = os.getenv("MODEL_VERSION", "dog_pose_yolo26m_best")
LABEL_STUDIO_DATA_DIR = Path(os.getenv("LABEL_STUDIO_DATA_DIR", "/label_studio_data"))
LOCAL_FILES_DOCUMENT_ROOT = Path(os.getenv("LOCAL_FILES_DOCUMENT_ROOT", "/local_files"))
DEFAULT_KEYPOINT_FROM_NAME = os.getenv("KEYPOINT_FROM_NAME", "pose_keypoints")
DEFAULT_VISIBILITY_FROM_NAME = os.getenv("VISIBILITY_FROM_NAME", "visibility")
DEFAULT_BBOX_FROM_NAME = os.getenv("BBOX_FROM_NAME", "bbox")
DEFAULT_TO_NAME = os.getenv("TO_NAME", "image")
DEFAULT_IMAGE_FIELD = os.getenv("IMAGE_FIELD", "image")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.20"))
KPT_CONFIDENCE_THRESHOLD = float(os.getenv("KPT_CONFIDENCE_THRESHOLD", "0.20"))
MAX_DETECTIONS = int(os.getenv("MAX_DETECTIONS", "10"))
DEVICE = os.getenv("DEVICE", "cpu")

# This order comes from the kpt_names stored in dog_pose_yolo26m_best.pt.
MODEL_KEYPOINT_LABELS = [
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

# The 17-point annotation profile used by cat_pose_annotation_seed_800. The
# first item in each pair is the source index name in the 24-point dog model;
# the second item is the Label Studio label shown to annotators.
REFERENCE_17_KEYPOINTS = [
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

MODEL_KEYPOINT_INDEX = {
    label: index for index, label in enumerate(MODEL_KEYPOINT_LABELS)
}
REFERENCE_LABEL_TO_MODEL = {
    output_label: model_label for model_label, output_label in REFERENCE_17_KEYPOINTS
}
DEFAULT_KEYPOINT_OUTPUTS = list(enumerate(MODEL_KEYPOINT_LABELS))

app = Flask(__name__)
model = YOLO(MODEL_PATH)
model_names = model.names or {}
model_task = getattr(model, "task", None)
raw_kpt_shape = getattr(getattr(model, "model", None), "kpt_shape", None)
model_kpt_shape = list(raw_kpt_shape) if raw_kpt_shape is not None else None


def response_status():
    return {
        "status": "UP",
        "task": model_task,
        "model_version": MODEL_VERSION,
        "model_path": MODEL_PATH,
        "labels": model_names,
        "kpt_shape": model_kpt_shape,
        "keypoints": MODEL_KEYPOINT_LABELS,
        "reference_17_keypoints": [label for _, label in REFERENCE_17_KEYPOINTS],
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "kpt_confidence_threshold": KPT_CONFIDENCE_THRESHOLD,
        "device": DEVICE,
    }


@app.get("/")
@app.get("/health")
def health():
    return jsonify(response_status())


@app.post("/setup")
def setup():
    return jsonify({"status": "ok", "model_version": MODEL_VERSION})


@app.post("/predict")
def predict():
    payload = request.get_json(force=True, silent=True) or {}
    tasks = payload.get("tasks") or []
    label_config = payload.get("label_config") or payload.get("schema") or ""
    tag_names = parse_label_config(label_config)

    predictions = []
    for task in tasks:
        temporary_path = None
        try:
            image_path, temporary_path = resolve_task_image(task, tag_names["image_field"])
            prediction = predict_one(image_path, tag_names)
        except Exception as exc:
            app.logger.exception("Prediction failed for task %s", task.get("id"))
            prediction = {
                "result": [],
                "score": 0.0,
                "model_version": MODEL_VERSION,
                "error": str(exc),
            }
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        predictions.append(prediction)

    return jsonify({"results": predictions})


def parse_label_config(label_config):
    defaults = {
        "keypoint_from_name": DEFAULT_KEYPOINT_FROM_NAME,
        "visibility_from_name": DEFAULT_VISIBILITY_FROM_NAME,
        "bbox_from_name": DEFAULT_BBOX_FROM_NAME,
        "to_name": DEFAULT_TO_NAME,
        "image_field": DEFAULT_IMAGE_FIELD,
        "keypoint_outputs": DEFAULT_KEYPOINT_OUTPUTS,
    }
    if not label_config:
        return defaults

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(label_config)
        image_tags = {tag.attrib.get("name"): tag for tag in root.iter() if tag.tag == "Image"}
        keypoint_tag = next((tag for tag in root.iter() if tag.tag == "KeyPointLabels"), None)
        bbox_tag = next((tag for tag in root.iter() if tag.tag == "RectangleLabels"), None)
        choices_tag = next(
            (
                tag
                for tag in root.iter()
                if tag.tag == "Choices" and tag.attrib.get("perRegion", "").lower() == "true"
            ),
            None,
        )

        region_tag = keypoint_tag or bbox_tag
        if region_tag is None:
            return defaults

        to_name = region_tag.attrib.get("toName", DEFAULT_TO_NAME)
        image_tag = image_tags.get(to_name)
        image_value = (
            image_tag.attrib.get("value", f"${DEFAULT_IMAGE_FIELD}")
            if image_tag is not None
            else f"${DEFAULT_IMAGE_FIELD}"
        )
        return {
            "keypoint_from_name": (
                keypoint_tag.attrib.get("name", DEFAULT_KEYPOINT_FROM_NAME)
                if keypoint_tag is not None
                else None
            ),
            "visibility_from_name": (
                choices_tag.attrib.get("name", DEFAULT_VISIBILITY_FROM_NAME)
                if choices_tag is not None
                else None
            ),
            "bbox_from_name": (
                bbox_tag.attrib.get("name", DEFAULT_BBOX_FROM_NAME) if bbox_tag is not None else None
            ),
            "to_name": to_name,
            "image_field": image_value[1:] if image_value.startswith("$") else image_value,
            "keypoint_outputs": configured_keypoint_outputs(keypoint_tag),
        }
    except Exception:
        app.logger.exception("Failed to parse label config, using defaults")
        return defaults


def configured_keypoint_outputs(keypoint_tag):
    if keypoint_tag is None:
        return []

    outputs = []
    used_model_labels = set()
    for child in keypoint_tag:
        if child.tag != "Label":
            continue
        output_label = child.attrib.get("value")
        if not output_label:
            continue

        model_label = REFERENCE_LABEL_TO_MODEL.get(output_label, output_label)
        model_index = MODEL_KEYPOINT_INDEX.get(model_label)
        if model_index is None or model_label in used_model_labels:
            continue

        outputs.append((model_index, output_label))
        used_model_labels.add(model_label)

    return outputs


def resolve_task_image(task, image_field):
    data = task.get("data") or {}
    meta = task.get("meta") or {}

    source_path = meta.get("source_path")
    if source_path:
        mapped_source = map_source_path(source_path)
        if mapped_source.exists():
            return mapped_source, None

    image_value = (
        data.get(image_field)
        or data.get(DEFAULT_IMAGE_FIELD)
        or data.get("$undefined$")
        or first_image_like_value(data)
    )
    if not image_value:
        raise ValueError(f"Task does not contain image field '{image_field}'")

    image_value = unquote(str(image_value))
    parsed = urlparse(image_value)
    if parsed.scheme in ("http", "https"):
        temporary_path = download_image(image_value)
        return temporary_path, temporary_path

    if parsed.path.startswith("/data/upload/"):
        relative = parsed.path.removeprefix("/data/")
        path = LABEL_STUDIO_DATA_DIR / "media" / relative
        if path.exists():
            return path, None

    if parsed.path.startswith("/data/local-files/"):
        local_file = first_query_value(parsed.query, "d")
        if local_file:
            path = LOCAL_FILES_DOCUMENT_ROOT / local_file
            if path.exists():
                return path, None

    direct_path = map_source_path(image_value)
    if direct_path.exists():
        return direct_path, None

    raise FileNotFoundError(f"Cannot resolve task image: {image_value}")


def first_query_value(query, key):
    values = parse_qs(query).get(key)
    return values[0] if values else None


def first_image_like_value(data):
    for value in data.values():
        if not isinstance(value, str):
            continue
        lower_value = value.lower()
        if (
            lower_value.startswith(("/data/upload/", "/data/local-files/"))
            or lower_value.startswith(("http://", "https://"))
            or lower_value.endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))
        ):
            return value
    return None


def map_source_path(value):
    normalized = str(value).replace("\\", "/")
    mappings = {
        "/root/label_studio/label_studio_data": LABEL_STUDIO_DATA_DIR,
        "/data/label_studio/local_files": LOCAL_FILES_DOCUMENT_ROOT,
    }
    for host_prefix, container_prefix in mappings.items():
        if normalized.startswith(host_prefix):
            relative = normalized[len(host_prefix) :].lstrip("/")
            return container_prefix / relative
    return Path(value)


def download_image(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    suffix = Path(urlparse(url).path).suffix or ".jpg"
    path = Path(tempfile.gettempdir()) / f"label-studio-dog-pose-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(response.content)
    return path


def predict_one(image_path, tag_names):
    with Image.open(image_path) as image:
        original_width, original_height = image.size

    yolo_results = model.predict(
        source=str(image_path),
        conf=CONFIDENCE_THRESHOLD,
        max_det=MAX_DETECTIONS,
        device=DEVICE,
        verbose=False,
    )

    best = None
    best_score = 0.0
    for yolo_result in yolo_results:
        boxes = getattr(yolo_result, "boxes", None)
        keypoints = getattr(yolo_result, "keypoints", None)
        if boxes is None or keypoints is None or len(boxes) == 0:
            continue
        for index, box in enumerate(boxes):
            confidence = float(box.conf.item())
            if best is None or confidence > best_score:
                best = (yolo_result, index)
                best_score = confidence

    if best is None:
        return {"result": [], "score": 0.0, "model_version": MODEL_VERSION}

    yolo_result, detection_index = best
    box = yolo_result.boxes[detection_index]
    xy = yolo_result.keypoints.xy[detection_index].cpu().numpy()
    keypoint_conf = yolo_result.keypoints.conf
    conf_values = (
        keypoint_conf[detection_index].cpu().numpy()
        if keypoint_conf is not None
        else [best_score] * len(xy)
    )

    results = []
    used_scores = []
    if tag_names["bbox_from_name"]:
        class_index = int(box.cls.item())
        object_label = str(model_names.get(class_index, class_index))
        x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
        results.append(
            {
                "id": uuid.uuid4().hex[:10],
                "from_name": tag_names["bbox_from_name"],
                "to_name": tag_names["to_name"],
                "type": "rectanglelabels",
                "origin": "prediction",
                "original_width": original_width,
                "original_height": original_height,
                "image_rotation": 0,
                "score": best_score,
                "value": {
                    "x": clamp_percent(x1 / original_width * 100.0),
                    "y": clamp_percent(y1 / original_height * 100.0),
                    "width": clamp_percent((x2 - x1) / original_width * 100.0),
                    "height": clamp_percent((y2 - y1) / original_height * 100.0),
                    "rotation": 0,
                    "rectanglelabels": [object_label],
                },
            }
        )
        used_scores.append(best_score)

    if tag_names["keypoint_from_name"]:
        for keypoint_index, label in tag_names["keypoint_outputs"]:
            if keypoint_index >= len(xy):
                continue
            x, y = float(xy[keypoint_index][0]), float(xy[keypoint_index][1])
            score = (
                float(conf_values[keypoint_index])
                if keypoint_index < len(conf_values)
                else best_score
            )
            if score < KPT_CONFIDENCE_THRESHOLD or (x <= 0.0 and y <= 0.0):
                continue

            region_id = uuid.uuid4().hex[:10]
            point_value = {
                "x": clamp_percent(x / original_width * 100.0),
                "y": clamp_percent(y / original_height * 100.0),
                "width": 0.3,
                "keypointlabels": [label],
            }
            common = {
                "id": region_id,
                "to_name": tag_names["to_name"],
                "origin": "prediction",
                "original_width": original_width,
                "original_height": original_height,
                "image_rotation": 0,
                "score": score,
            }
            results.append(
                {
                    **common,
                    "from_name": tag_names["keypoint_from_name"],
                    "type": "keypointlabels",
                    "value": point_value,
                }
            )
            if tag_names["visibility_from_name"]:
                results.append(
                    {
                        **common,
                        "from_name": tag_names["visibility_from_name"],
                        "type": "choices",
                        "value": {
                            "x": point_value["x"],
                            "y": point_value["y"],
                            "width": point_value["width"],
                            "choices": ["2"],
                        },
                    }
                )
            used_scores.append(score)

    prediction_score = sum(used_scores) / len(used_scores) if used_scores else best_score
    return {"result": results, "score": prediction_score, "model_version": MODEL_VERSION}


def clamp_percent(value):
    return max(0.0, min(100.0, value))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9090)
