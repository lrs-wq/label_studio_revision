import os
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from flask import Flask, jsonify, request
from PIL import Image
from ultralytics import YOLO


MODEL_PATH = os.getenv("MODEL_PATH", "/models/best.pt")
MODEL_VERSION = os.getenv("MODEL_VERSION", f"yolo-pose:{Path(MODEL_PATH).name}")
LABEL_STUDIO_DATA_DIR = Path(os.getenv("LABEL_STUDIO_DATA_DIR", "/label_studio_data"))
LOCAL_FILES_DOCUMENT_ROOT = Path(os.getenv("LOCAL_FILES_DOCUMENT_ROOT", "/local_files"))
DEFAULT_KEYPOINT_FROM_NAME = os.getenv("KEYPOINT_FROM_NAME", "pose_keypoints")
DEFAULT_VISIBILITY_FROM_NAME = os.getenv("VISIBILITY_FROM_NAME", "visibility")
DEFAULT_TO_NAME = os.getenv("TO_NAME", "image")
DEFAULT_IMAGE_FIELD = os.getenv("IMAGE_FIELD", "image")
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.20"))
KPT_CONFIDENCE_THRESHOLD = float(os.getenv("KPT_CONFIDENCE_THRESHOLD", "0.20"))
MAX_DETECTIONS = int(os.getenv("MAX_DETECTIONS", "10"))
MAX_KEYPOINTS = int(os.getenv("MAX_KEYPOINTS", "0"))

KEYPOINT_LABELS = [
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

app = Flask(__name__)
model = YOLO(MODEL_PATH)
model_names = model.names or {}


def response_status():
    return {
        "status": "UP",
        "task": "pose",
        "model_version": MODEL_VERSION,
        "model_path": MODEL_PATH,
        "labels": model_names,
        "keypoints": KEYPOINT_LABELS,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "kpt_confidence_threshold": KPT_CONFIDENCE_THRESHOLD,
        "max_keypoints": MAX_KEYPOINTS,
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
    keypoint_from_name, visibility_from_name, to_name, image_field = parse_label_config(label_config)

    results = []
    for task in tasks:
        try:
            image_path = resolve_task_image(task, image_field)
            prediction = predict_one(image_path, keypoint_from_name, visibility_from_name, to_name)
        except Exception as exc:
            app.logger.exception("Prediction failed for task %s", task.get("id"))
            prediction = {
                "result": [],
                "score": 0.0,
                "model_version": MODEL_VERSION,
                "error": str(exc),
            }
        results.append(prediction)

    return jsonify({"results": results})


def parse_label_config(label_config):
    if not label_config:
        return (
            DEFAULT_KEYPOINT_FROM_NAME,
            DEFAULT_VISIBILITY_FROM_NAME,
            DEFAULT_TO_NAME,
            DEFAULT_IMAGE_FIELD,
        )

    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(label_config)
        image_tags = {tag.attrib.get("name"): tag for tag in root.iter() if tag.tag == "Image"}
        keypoint_tag = next((tag for tag in root.iter() if tag.tag == "KeyPointLabels"), None)
        choices_tag = next((tag for tag in root.iter() if tag.tag == "Choices"), None)
        if keypoint_tag is None:
            return (
                DEFAULT_KEYPOINT_FROM_NAME,
                DEFAULT_VISIBILITY_FROM_NAME,
                DEFAULT_TO_NAME,
                DEFAULT_IMAGE_FIELD,
            )

        keypoint_from_name = keypoint_tag.attrib.get("name", DEFAULT_KEYPOINT_FROM_NAME)
        to_name = keypoint_tag.attrib.get("toName", DEFAULT_TO_NAME)
        visibility_from_name = (
            choices_tag.attrib.get("name", DEFAULT_VISIBILITY_FROM_NAME)
            if choices_tag is not None
            else DEFAULT_VISIBILITY_FROM_NAME
        )
        image_tag = image_tags.get(to_name)
        image_value = image_tag.attrib.get("value", f"${DEFAULT_IMAGE_FIELD}") if image_tag is not None else f"${DEFAULT_IMAGE_FIELD}"
        image_field = image_value[1:] if image_value.startswith("$") else image_value
        return keypoint_from_name, visibility_from_name, to_name, image_field
    except Exception:
        app.logger.exception("Failed to parse label config, using defaults")
        return (
            DEFAULT_KEYPOINT_FROM_NAME,
            DEFAULT_VISIBILITY_FROM_NAME,
            DEFAULT_TO_NAME,
            DEFAULT_IMAGE_FIELD,
        )


def resolve_task_image(task, image_field):
    data = task.get("data") or {}
    meta = task.get("meta") or {}

    source_path = meta.get("source_path")
    if source_path:
        mapped_source = map_windows_path(source_path)
        if mapped_source.exists():
            return mapped_source

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
        return download_image(image_value)

    if parsed.path.startswith("/data/upload/"):
        relative = parsed.path.removeprefix("/data/")
        path = LABEL_STUDIO_DATA_DIR / "media" / relative
        if path.exists():
            return path

    if parsed.path.startswith("/data/local-files/"):
        local_file = first_query_value(parsed.query, "d")
        if local_file:
            path = LOCAL_FILES_DOCUMENT_ROOT / local_file
            if path.exists():
                return path

    direct_path = map_windows_path(image_value)
    if direct_path.exists():
        return direct_path

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


def map_windows_path(value):
    normalized = str(value).replace("\\", "/")
    mappings = {
        "D:/gogelila/label_studio/label_studio_data": LABEL_STUDIO_DATA_DIR,
        "D:/gogelila/Eating_behavior/data": LOCAL_FILES_DOCUMENT_ROOT / "data",
        "D:/gogelila/Eating_behavior/yolo": LOCAL_FILES_DOCUMENT_ROOT / "yolo",
    }
    for windows_prefix, linux_prefix in mappings.items():
        if normalized.lower().startswith(windows_prefix.lower()):
            relative = normalized[len(windows_prefix):].lstrip("/")
            return linux_prefix / relative

    return Path(value)


def download_image(url):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    suffix = Path(urlparse(url).path).suffix or ".jpg"
    path = Path(tempfile.gettempdir()) / f"label-studio-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(response.content)
    return path


def predict_one(image_path, keypoint_from_name, visibility_from_name, to_name):
    with Image.open(image_path) as image:
        original_width, original_height = image.size

    yolo_results = model.predict(
        source=str(image_path),
        conf=CONFIDENCE_THRESHOLD,
        max_det=MAX_DETECTIONS,
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

    yolo_result, det_index = best
    xy = yolo_result.keypoints.xy[det_index].cpu().numpy()
    conf = yolo_result.keypoints.conf
    conf_values = conf[det_index].cpu().numpy() if conf is not None else [best_score] * len(xy)

    candidates = []
    for kpt_index, label in enumerate(KEYPOINT_LABELS):
        if kpt_index >= len(xy):
            continue
        x, y = float(xy[kpt_index][0]), float(xy[kpt_index][1])
        kpt_score = float(conf_values[kpt_index]) if kpt_index < len(conf_values) else best_score
        if kpt_score < KPT_CONFIDENCE_THRESHOLD:
            continue
        if x <= 0.0 and y <= 0.0:
            continue
        if original_width <= 0 or original_height <= 0:
            continue

        candidates.append((kpt_score, kpt_index, label, x, y))

    if MAX_KEYPOINTS > 0:
        candidates = sorted(candidates, key=lambda item: item[0], reverse=True)[:MAX_KEYPOINTS]
    candidates = sorted(candidates, key=lambda item: item[1])

    results = []
    used_scores = []
    for kpt_score, _kpt_index, label, x, y in candidates:
        region_id = uuid.uuid4().hex[:10]
        point_value = {
            "x": clamp_percent(x / original_width * 100.0),
            "y": clamp_percent(y / original_height * 100.0),
            "width": 0.3,
            "keypointlabels": [label],
        }
        common = {
            "id": region_id,
            "to_name": to_name,
            "origin": "prediction",
            "original_width": original_width,
            "original_height": original_height,
            "image_rotation": 0,
            "score": kpt_score,
        }
        results.append(
            {
                **common,
                "from_name": keypoint_from_name,
                "type": "keypointlabels",
                "value": point_value,
            }
        )
        results.append(
            {
                **common,
                "from_name": visibility_from_name,
                "type": "choices",
                "value": {
                    "x": point_value["x"],
                    "y": point_value["y"],
                    "width": point_value["width"],
                    "choices": ["2"],
                },
            }
        )
        used_scores.append(kpt_score)

    score = sum(used_scores) / len(used_scores) if used_scores else best_score
    return {"result": results, "score": score, "model_version": MODEL_VERSION}


def clamp_percent(value):
    return max(0.0, min(100.0, value))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9090)
