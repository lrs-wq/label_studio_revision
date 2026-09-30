import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image


PROJECT_ID = 8
PROJECT_TITLE = "cat_pose_annotation_seed_230"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
MEDIA_ROOT = DATA_DIR / "media"
EXPORT_ROOT = Path(r"D:\gogelila\Eating_behavior\data\cat_pose_v1")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXCLUDED_SPLITS = {"test"}

KEYPOINTS = [
    "鼻尖",
    "左眼",
    "右眼",
    "颈背",
    "尾根",
    "左前肩",
    "左前肘",
    "左前爪",
    "右前肩",
    "右前肘",
    "右前爪",
    "左后髋",
    "左后膝",
    "左后爪",
    "右后髋",
    "右后膝",
    "右后爪",
]


def backup_existing_export():
    if EXPORT_ROOT.exists() and any(EXPORT_ROOT.iterdir()):
        shutil.rmtree(EXPORT_ROOT)
        print(f"existing_export_removed={EXPORT_ROOT}")
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


def image_path_from_task_data(data):
    image_value = data.get("image", "")
    if image_value.startswith("/data/"):
        return MEDIA_ROOT / image_value.removeprefix("/data/")
    return MEDIA_ROOT / image_value.lstrip("/")


def fetch_latest_completed_tasks():
    query = """
    with ranked_annotations as (
        select
            c.id,
            c.task_id,
            c.result,
            c.updated_at,
            row_number() over (
                partition by c.task_id
                order by c.updated_at desc, c.id desc
            ) as rn
        from task_completion c
        where c.project_id = ? and c.was_cancelled = 0
    )
    select
        t.id as task_id,
        t.data,
        a.id as annotation_id,
        a.result,
        a.updated_at
    from ranked_annotations a
    join task t on t.id = a.task_id
    where a.rn = 1 and t.project_id = ?
    order by t.id
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, (PROJECT_ID, PROJECT_ID))]


def parse_points(result_json, fallback_width, fallback_height):
    result = json.loads(result_json or "[]")
    visibility_by_id = {}
    for item in result:
        if item.get("type") != "choices":
            continue
        value = item.get("value") or {}
        choices = value.get("choices") or []
        if choices:
            visibility_by_id[item.get("id")] = str(choices[0])

    points = {}
    duplicates = []
    for item in result:
        if item.get("type") != "keypointlabels":
            continue
        value = item.get("value") or {}
        labels = value.get("keypointlabels") or []
        if not labels:
            continue
        label = labels[0]
        if label in points:
            duplicates.append(label)
            continue

        width = int(item.get("original_width") or fallback_width)
        height = int(item.get("original_height") or fallback_height)
        x = float(value["x"]) / 100.0
        y = float(value["y"]) / 100.0
        x = min(max(x, 0.0), 1.0)
        y = min(max(y, 0.0), 1.0)
        v = visibility_by_id.get(item.get("id"), "2")
        if v not in {"1", "2"}:
            v = "2"
        points[label] = {
            "x": x,
            "y": y,
            "v": v,
            "width": width,
            "height": height,
        }
    return points, duplicates


def safe_relative_path(data):
    split = (data.get("split") or "train").replace("\\", "/").strip("/")
    relative_path = (data.get("relative_path") or data.get("filename") or "").replace("\\", "/").strip("/")
    if not relative_path:
        raise RuntimeError("Task is missing filename/relative_path")

    parts = relative_path.split("/")
    if parts and parts[0] in {"train", "val", "test"}:
        image_relative = Path(*parts[1:])
    else:
        image_relative = Path(*parts)
    return split, image_relative


def yolo_line(points):
    values = ["0", "0.5", "0.5", "1.0", "1.0"]
    for label in KEYPOINTS:
        point = points.get(label)
        if point is None:
            values.extend(["0", "0", "0"])
        else:
            values.extend([f"{point['x']:.6f}", f"{point['y']:.6f}", point["v"]])
    return " ".join(values)


def copy_image(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def write_data_yaml(split_counts):
    lines = [
        f"path: {EXPORT_ROOT.as_posix()}",
        "train: images/train",
        "val: images/val",
    ]
    lines.extend(
        [
            "names:",
            "  0: cat",
            f"kpt_shape: [{len(KEYPOINTS)}, 3]",
            "flip_idx: [0, 2, 1, 3, 4, 8, 9, 10, 5, 6, 7, 14, 15, 16, 11, 12, 13]",
            "keypoints:",
        ]
    )
    for index, label in enumerate(KEYPOINTS):
        lines.append(f"  {index}: {label}")
    (EXPORT_ROOT / "data.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    if not DB_PATH.is_file():
        raise FileNotFoundError(DB_PATH)

    rows = fetch_latest_completed_tasks()
    backup_existing_export()

    split_counts = Counter()
    keypoint_counts = Counter()
    visibility_counts = Counter()
    skipped_by_split = Counter()
    missing_images = []
    duplicate_labels = []
    exported_rows = []
    used_image_destinations = set()

    for row in rows:
        data = json.loads(row["data"])
        source_image = image_path_from_task_data(data)
        if not source_image.is_file():
            missing_images.append({"task_id": row["task_id"], "image": str(source_image)})
            continue
        if source_image.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        split, image_relative = safe_relative_path(data)
        if split in EXCLUDED_SPLITS:
            skipped_by_split[split] += 1
            continue

        image_destination = EXPORT_ROOT / "images" / split / image_relative
        if image_destination in used_image_destinations:
            stem = image_relative.stem
            suffix = image_relative.suffix
            image_relative = image_relative.with_name(f"{stem}__task{row['task_id']}{suffix}")
            image_destination = EXPORT_ROOT / "images" / split / image_relative
        used_image_destinations.add(image_destination)

        with Image.open(source_image) as img:
            fallback_width, fallback_height = img.size

        points, duplicates = parse_points(row["result"], fallback_width, fallback_height)
        if duplicates:
            duplicate_labels.append({"task_id": row["task_id"], "labels": duplicates})

        label_destination = EXPORT_ROOT / "labels" / split / image_relative.with_suffix(".txt")
        copy_image(source_image, image_destination)
        label_destination.parent.mkdir(parents=True, exist_ok=True)
        label_destination.write_text(yolo_line(points) + "\n", encoding="utf-8")

        split_counts[split] += 1
        for label, point in points.items():
            keypoint_counts[label] += 1
            visibility_counts[point["v"]] += 1
        exported_rows.append(
            {
                "task_id": row["task_id"],
                "annotation_id": row["annotation_id"],
                "split": split,
                "filename": data.get("filename"),
                "relative_path": data.get("relative_path"),
                "image": str(image_destination),
                "label": str(label_destination),
                "keypoints": len(points),
            }
        )

    write_data_yaml(split_counts)

    summary = {
        "project_id": PROJECT_ID,
        "project_title": PROJECT_TITLE,
        "export_root": str(EXPORT_ROOT),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "completed_annotations": len(rows),
        "exported_images": len(exported_rows),
        "excluded_splits": sorted(EXCLUDED_SPLITS),
        "skipped_by_split": dict(sorted(skipped_by_split.items())),
        "missing_images": missing_images,
        "duplicate_labels": duplicate_labels,
        "bbox_policy": "full_image_bbox: class 0 0.5 0.5 1.0 1.0",
        "keypoints": KEYPOINTS,
        "split_counts": dict(sorted(split_counts.items())),
        "keypoint_counts": {label: keypoint_counts.get(label, 0) for label in KEYPOINTS},
        "visibility_counts": dict(sorted(visibility_counts.items())),
        "rows": exported_rows,
    }
    (EXPORT_ROOT / "export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
