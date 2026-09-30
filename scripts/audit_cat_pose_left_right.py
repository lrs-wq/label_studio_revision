import csv
import json
import math
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


DATA_DIR = Path(r"D:\gogelila\label_studio\label_studio_data")
DB_PATH = DATA_DIR / "label_studio.sqlite3"
MEDIA_ROOT = DATA_DIR / "media"
REPORT_ROOT = DATA_DIR / "reports"
PROJECT_TITLE = "cat_pose_annotation_seed_230"

LABELS = [
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

ABBR = {
    "\u9f3b\u5c16": "N",
    "\u5de6\u773c": "LE",
    "\u53f3\u773c": "RE",
    "\u9888\u80cc": "Ne",
    "\u5c3e\u6839": "T",
    "\u5de6\u524d\u80a9": "LFS",
    "\u5de6\u524d\u8098": "LFE",
    "\u5de6\u524d\u722a": "LFP",
    "\u53f3\u524d\u80a9": "RFS",
    "\u53f3\u524d\u8098": "RFE",
    "\u53f3\u524d\u722a": "RFP",
    "\u5de6\u540e\u9acb": "LHH",
    "\u5de6\u540e\u819d": "LHK",
    "\u5de6\u540e\u722a": "LHP",
    "\u53f3\u540e\u9acb": "RHH",
    "\u53f3\u540e\u819d": "RHK",
    "\u53f3\u540e\u722a": "RHP",
}

COLORS = {
    "N": (255, 60, 60),
    "LE": (255, 80, 210),
    "RE": (80, 150, 255),
    "Ne": (255, 210, 0),
    "T": (0, 210, 255),
    "LFS": (255, 100, 100),
    "LFE": (255, 140, 140),
    "LFP": (255, 190, 190),
    "RFS": (70, 220, 130),
    "RFE": (110, 235, 160),
    "RFP": (170, 250, 200),
    "LHH": (255, 130, 0),
    "LHK": (255, 170, 50),
    "LHP": (255, 210, 120),
    "RHH": (150, 80, 255),
    "RHK": (180, 120, 255),
    "RHP": (210, 170, 255),
}


def dist(a, b):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def pct_to_px(item, width, height):
    return {
        "x": item["x"] * width / 100.0,
        "y": item["y"] * height / 100.0,
        "v": item.get("v", "2"),
        "id": item.get("id", ""),
    }


def image_path_from_task_data(data):
    image_value = data.get("image", "")
    if image_value.startswith("/data/"):
        return MEDIA_ROOT / image_value.removeprefix("/data/")
    return MEDIA_ROOT / image_value.lstrip("/")


def load_rows():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        project = conn.execute(
            "select id, title from project where title=?",
            (PROJECT_TITLE,),
        ).fetchone()
        if project is None:
            raise RuntimeError(f"Project not found: {PROJECT_TITLE}")

        rows = conn.execute(
            """
            select
                c.id as completion_id,
                c.task_id,
                c.updated_at,
                c.result,
                t.data
            from task_completion c
            join task t on t.id = c.task_id
            where t.project_id = ? and c.was_cancelled = 0
            order by c.task_id
            """,
            (project["id"],),
        ).fetchall()
    return project, rows


def parse_completion(row):
    data = json.loads(row["data"])
    result = json.loads(row["result"] or "[]")

    points = {}
    visibility_by_id = {}
    for item in result:
        value = item.get("value") or {}
        if item.get("type") == "choices":
            choices = value.get("choices") or []
            if choices:
                visibility_by_id[item.get("id")] = str(choices[0])

    for item in result:
        if item.get("type") != "keypointlabels":
            continue
        value = item.get("value") or {}
        labels = value.get("keypointlabels") or []
        if not labels:
            continue
        label = labels[0]
        width = item.get("original_width") or int(data.get("width") or 0)
        height = item.get("original_height") or int(data.get("height") or 0)
        point = pct_to_px(
            {
                "x": float(value["x"]),
                "y": float(value["y"]),
                "v": visibility_by_id.get(item.get("id"), "2"),
                "id": item.get("id"),
            },
            width,
            height,
        )
        point["width"] = width
        point["height"] = height
        points[label] = point

    return {
        "task_id": row["task_id"],
        "completion_id": row["completion_id"],
        "updated_at": row["updated_at"],
        "data": data,
        "image_path": image_path_from_task_data(data),
        "filename": data.get("filename", ""),
        "relative_path": data.get("relative_path", ""),
        "points": points,
    }


def ratio_check_same_vs_swapped(points, left_anchor, right_anchor, left_tip, right_tip):
    required = [left_anchor, right_anchor, left_tip, right_tip]
    if not all(label in points for label in required):
        return None
    same = dist(points[left_anchor], points[left_tip]) + dist(points[right_anchor], points[right_tip])
    swapped = dist(points[left_anchor], points[right_tip]) + dist(points[right_anchor], points[left_tip])
    if same <= 0 or swapped <= 0:
        return None
    return {
        "same": same,
        "swapped": swapped,
        "ratio": swapped / same,
    }


def add_issue(issues, severity, code, reason):
    issues.append({"severity": severity, "code": code, "reason": reason})


def audit_item(item):
    p = item["points"]
    issues = []

    # Cat anatomical left/right: on normal front-facing views the cat's left eye
    # usually appears on the right side of the image.
    if "\u5de6\u773c" in p and "\u53f3\u773c" in p:
        le = p["\u5de6\u773c"]
        re = p["\u53f3\u773c"]
        eye_sep = dist(le, re)
        img_diag = math.hypot(le["width"], le["height"])
        if eye_sep < img_diag * 0.015:
            add_issue(
                issues,
                "ERROR",
                "eye_too_close",
                "\u5de6\u773c\u548c\u53f3\u773c\u8ddd\u79bb\u8fc7\u8fd1\uff0c\u53ef\u80fd\u91cd\u53e0\u6216\u70b9\u9519\u3002",
            )
        elif le["x"] < re["x"] - img_diag * 0.01:
            add_issue(
                issues,
                "WARN",
                "eye_lr_order",
                "\u5de6\u773c\u5728\u753b\u9762\u5de6\u4fa7\u3001\u53f3\u773c\u5728\u753b\u9762\u53f3\u4fa7\uff1b\u6309\u732b\u81ea\u8eab\u5de6\u53f3\u89c4\u5219\uff0c\u6b63\u8138/\u8fd1\u6b63\u8138\u901a\u5e38\u5e94\u76f8\u53cd\uff0c\u9700\u590d\u6838\u662f\u5426\u5de6\u53f3\u773c\u4e92\u6362\u3002",
            )
        if "\u9f3b\u5c16" in p:
            n = p["\u9f3b\u5c16"]
            min_eye_x = min(le["x"], re["x"])
            max_eye_x = max(le["x"], re["x"])
            margin = max(eye_sep * 0.45, img_diag * 0.015)
            if n["x"] < min_eye_x - margin or n["x"] > max_eye_x + margin:
                add_issue(
                    issues,
                    "WARN",
                    "nose_outside_eyes",
                    "\u9f3b\u5c16\u6c34\u5e73\u4f4d\u7f6e\u660e\u663e\u8d85\u51fa\u53cc\u773c\u8303\u56f4\uff0c\u53ef\u80fd\u662f\u4fa7\u8138\uff0c\u4e5f\u53ef\u80fd\u6709\u773c/\u9f3b\u70b9\u4f4d\u504f\u5dee\u3002",
                )

    checks = [
        (
            "front_paw_vs_shoulder",
            "\u5de6\u524d\u80a9",
            "\u53f3\u524d\u80a9",
            "\u5de6\u524d\u722a",
            "\u53f3\u524d\u722a",
            "\u5de6/\u53f3\u524d\u722a\u5230\u5bf9\u4fa7\u524d\u80a9\u66f4\u8fd1\uff0c\u7591\u4f3c\u524d\u722a\u5de6\u53f3\u4e92\u6362\u6216\u817f\u4ea4\u53c9\u3002",
        ),
        (
            "front_paw_vs_elbow",
            "\u5de6\u524d\u8098",
            "\u53f3\u524d\u8098",
            "\u5de6\u524d\u722a",
            "\u53f3\u524d\u722a",
            "\u5de6/\u53f3\u524d\u722a\u5230\u5bf9\u4fa7\u524d\u8098\u66f4\u8fd1\uff0c\u7591\u4f3c\u524d\u722a\u6216\u524d\u8098\u5de6\u53f3\u4e92\u6362\u3002",
        ),
        (
            "hind_paw_vs_hip",
            "\u5de6\u540e\u9acb",
            "\u53f3\u540e\u9acb",
            "\u5de6\u540e\u722a",
            "\u53f3\u540e\u722a",
            "\u5de6/\u53f3\u540e\u722a\u5230\u5bf9\u4fa7\u540e\u9acb\u66f4\u8fd1\uff0c\u7591\u4f3c\u540e\u722a\u5de6\u53f3\u4e92\u6362\u6216\u59ff\u6001\u4ea4\u53c9\u3002",
        ),
        (
            "hind_paw_vs_knee",
            "\u5de6\u540e\u819d",
            "\u53f3\u540e\u819d",
            "\u5de6\u540e\u722a",
            "\u53f3\u540e\u722a",
            "\u5de6/\u53f3\u540e\u722a\u5230\u5bf9\u4fa7\u540e\u819d\u66f4\u8fd1\uff0c\u7591\u4f3c\u540e\u722a\u6216\u540e\u819d\u5de6\u53f3\u4e92\u6362\u3002",
        ),
    ]
    for code, la, ra, lt, rt, reason in checks:
        metric = ratio_check_same_vs_swapped(p, la, ra, lt, rt)
        if not metric:
            continue
        if metric["ratio"] < 0.75:
            add_issue(issues, "ERROR", code, f"{reason} swapped/same={metric['ratio']:.2f}")
        elif metric["ratio"] < 0.92:
            add_issue(issues, "WARN", code, f"{reason} swapped/same={metric['ratio']:.2f}")

    for left, right, code, desc in [
        ("\u5de6\u524d\u722a", "\u53f3\u524d\u722a", "front_paw_duplicate", "\u5de6/\u53f3\u524d\u722a"),
        ("\u5de6\u540e\u722a", "\u53f3\u540e\u722a", "hind_paw_duplicate", "\u5de6/\u53f3\u540e\u722a"),
    ]:
        if left in p and right in p:
            d = dist(p[left], p[right])
            diag = math.hypot(p[left]["width"], p[left]["height"])
            if d < diag * 0.025:
                add_issue(issues, "WARN", code, f"{desc}\u8ddd\u79bb\u8fc7\u8fd1\uff0c\u9700\u786e\u8ba4\u662f\u5426\u91cd\u590d\u70b9\u5230\u540c\u4e00\u53ea\u722a\u3002")

    left_labels = [label for label in LABELS if label.startswith("\u5de6")]
    right_labels = [label for label in LABELS if label.startswith("\u53f3")]
    item["left_count"] = sum(1 for label in left_labels if label in p)
    item["right_count"] = sum(1 for label in right_labels if label in p)
    item["issues"] = issues
    return item


def severity_of(item):
    severities = {issue["severity"] for issue in item["issues"]}
    if "ERROR" in severities:
        return "ERROR"
    if "WARN" in severities:
        return "WARN"
    return "OK"


def load_font(size=16):
    candidates = [
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_overlay(item, out_path, max_side=520):
    img = Image.open(item["image_path"]).convert("RGB")
    w, h = img.size
    scale = min(max_side / max(w, h), 1.0)
    if scale != 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)
    font = load_font(15)
    small_font = load_font(13)

    points = item["points"]
    scaled = {}
    for label, point in points.items():
        abbr = ABBR.get(label, "?")
        x = point["x"] * img.size[0] / w
        y = point["y"] * img.size[1] / h
        scaled[label] = (x, y)
        color = COLORS.get(abbr, (255, 255, 255))
        r = 5
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline=(0, 0, 0), width=1)
        text = f"{abbr}{point.get('v', '2')}"
        bbox = draw.textbbox((x + 6, y - 8), text, font=font)
        draw.rectangle((bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1), fill=(0, 0, 0))
        draw.text((x + 6, y - 8), text, fill=color, font=font)

    skeleton = [
        ("\u9f3b\u5c16", "\u5de6\u773c"),
        ("\u9f3b\u5c16", "\u53f3\u773c"),
        ("\u9f3b\u5c16", "\u9888\u80cc"),
        ("\u9888\u80cc", "\u5c3e\u6839"),
        ("\u5de6\u524d\u80a9", "\u5de6\u524d\u8098"),
        ("\u5de6\u524d\u8098", "\u5de6\u524d\u722a"),
        ("\u53f3\u524d\u80a9", "\u53f3\u524d\u8098"),
        ("\u53f3\u524d\u8098", "\u53f3\u524d\u722a"),
        ("\u5de6\u540e\u9acb", "\u5de6\u540e\u819d"),
        ("\u5de6\u540e\u819d", "\u5de6\u540e\u722a"),
        ("\u53f3\u540e\u9acb", "\u53f3\u540e\u819d"),
        ("\u53f3\u540e\u819d", "\u53f3\u540e\u722a"),
    ]
    for a, b in skeleton:
        if a in scaled and b in scaled:
            draw.line((scaled[a], scaled[b]), fill=(255, 255, 255), width=2)

    severity = severity_of(item)
    issue_text = "; ".join(issue["code"] for issue in item["issues"][:3]) or "OK"
    header = f"{item['task_id']} {severity} {issue_text}"
    bbox = draw.textbbox((5, 5), header, font=small_font)
    bg = (150, 30, 30) if severity == "ERROR" else (120, 90, 0) if severity == "WARN" else (0, 90, 40)
    draw.rectangle((0, 0, img.size[0], bbox[3] + 8), fill=bg)
    draw.text((5, 4), header, fill=(255, 255, 255), font=small_font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)
    return out_path


def make_contact_sheet(items, image_paths, out_path, cols=4, tile_w=360, tile_h=300):
    font = load_font(14)
    rows = math.ceil(len(items) / cols)
    sheet = Image.new("RGB", (cols * tile_w, rows * tile_h), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    for idx, (item, path) in enumerate(zip(items, image_paths)):
        img = Image.open(path).convert("RGB")
        scale = min((tile_w - 10) / img.size[0], (tile_h - 34) / img.size[1])
        img = img.resize((max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))), Image.Resampling.LANCZOS)
        x0 = (idx % cols) * tile_w
        y0 = (idx // cols) * tile_h
        sheet.paste(img, (x0 + 5, y0 + 28))
        title = f"{item['task_id']} {severity_of(item)}"
        draw.text((x0 + 6, y0 + 6), title, fill=(0, 0, 0), font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=92)
    return out_path


def write_reports(items, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "left_right_audit.csv"
    md_path = out_dir / "left_right_audit.md"
    json_path = out_dir / "left_right_audit.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["task_id", "severity", "filename", "relative_path", "left_count", "right_count", "issue_codes", "reasons"])
        for item in items:
            writer.writerow(
                [
                    item["task_id"],
                    severity_of(item),
                    item["filename"],
                    item["relative_path"],
                    item["left_count"],
                    item["right_count"],
                    ";".join(issue["code"] for issue in item["issues"]),
                    " | ".join(issue["reason"] for issue in item["issues"]),
                ]
            )

    payload = []
    for item in items:
        payload.append(
            {
                "task_id": item["task_id"],
                "completion_id": item["completion_id"],
                "severity": severity_of(item),
                "filename": item["filename"],
                "relative_path": item["relative_path"],
                "image_path": str(item["image_path"]),
                "left_count": item["left_count"],
                "right_count": item["right_count"],
                "issues": item["issues"],
                "points": {
                    label: {"x": round(point["x"], 2), "y": round(point["y"], 2), "v": point.get("v", "2")}
                    for label, point in item["points"].items()
                },
            }
        )
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = Counter(severity_of(item) for item in items)
    issue_counts = Counter(issue["code"] for item in items for issue in item["issues"])
    lines = [
        "# cat_pose_annotation_seed_230 左右关键点审计",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 已完成标注：{len(items)}",
        f"- OK：{counts.get('OK', 0)}",
        f"- WARN：{counts.get('WARN', 0)}",
        f"- ERROR：{counts.get('ERROR', 0)}",
        "",
        "## 规则说明",
        "",
        "- 左右按猫自身左右，不按画面左右。",
        "- 正脸或近正脸情况下，猫的左眼通常在画面右侧、右眼在画面左侧。",
        "- 爪子检查主要比较同侧肩/肘/髋/膝到同侧爪的距离是否比交叉匹配更合理；交叉站姿会产生保守 WARN。",
        "- 该报告是筛查结果，最终仍以人工看图复核为准。",
        "",
        "## 问题类型统计",
        "",
        "| code | count |",
        "|---|---:|",
    ]
    for code, count in issue_counts.most_common():
        lines.append(f"| `{code}` | {count} |")

    lines.extend(["", "## 可疑标注", "", "| task_id | severity | 文件 | 原因 |", "|---:|---|---|---|"])
    for item in items:
        if not item["issues"]:
            continue
        reasons = "<br>".join(f"`{issue['code']}` {issue['reason']}" for issue in item["issues"])
        lines.append(f"| {item['task_id']} | {severity_of(item)} | `{item['filename']}` | {reasons} |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return csv_path, md_path, json_path


def main():
    _, rows = load_rows()
    items = [audit_item(parse_completion(row)) for row in rows]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = REPORT_ROOT / f"pose_left_right_audit_project8_{timestamp}"
    overlay_dir = out_dir / "overlays"
    suspicious = [item for item in items if item["issues"]]

    overlay_paths = []
    for item in suspicious:
        overlay_paths.append(draw_overlay(item, overlay_dir / f"{item['task_id']}_{severity_of(item)}.jpg"))

    if suspicious:
        make_contact_sheet(suspicious[:80], overlay_paths[:80], out_dir / "left_right_suspects_contact_sheet.jpg")

    csv_path, md_path, json_path = write_reports(items, out_dir)

    summary = {
        "report_dir": str(out_dir),
        "completed_annotations": len(items),
        "severity_counts": dict(Counter(severity_of(item) for item in items)),
        "issue_counts": dict(Counter(issue["code"] for item in items for issue in item["issues"])),
        "csv": str(csv_path),
        "markdown": str(md_path),
        "json": str(json_path),
        "contact_sheet": str(out_dir / "left_right_suspects_contact_sheet.jpg") if suspicious else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
