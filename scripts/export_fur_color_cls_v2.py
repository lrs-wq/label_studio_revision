import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ID = 7
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SOURCE_ROOT = Path(r"D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls")
EXPORT_ROOT = Path(r"D:\gogelila\Eating_behavior\data\cls_v2")

LABEL_DISPLAY_NAMES = {
    "mixed_white_ragdoll": "混白/布偶",
    "white_cream_point": "白/奶油/重点色",
    "silver_gray_white": "银白/灰白",
    "gray_brown_tabby": "灰/棕虎斑",
    "orange_cream": "橘/金/奶油",
    "black_dark": "黑/深色",
    "black_white": "黑白",
    "calico_tortie": "三花/玳瑁",
    "hairless_cat": "无毛猫",
    "blue_cat": "蓝猫",
    "mixed_other": "混合/不确定",
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


def prepare_export_root():
    if EXPORT_ROOT.exists() and any(EXPORT_ROOT.iterdir()):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_root = EXPORT_ROOT.with_name(f"{EXPORT_ROOT.name}_before_export_{timestamp}")
        EXPORT_ROOT.rename(backup_root)
        print(f"existing_export_moved={backup_root}")
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


def fetch_labeled_tasks():
    query = """
    with ranked_annotations as (
        select
            c.id,
            c.task_id,
            c.result,
            row_number() over (
                partition by c.task_id
                order by c.updated_at desc, c.id desc
            ) as rn
        from task_completion c
        where c.project_id = ? and c.was_cancelled = 0
    )
    select
        t.id as task_id,
        json_extract(t.data, '$.filename') as filename,
        json_extract(t.data, '$.fur_color_source') as fur_color_source,
        json_extract(t.data, '$.original_split') as original_split,
        json_extract(r.value, '$.value.choices[0]') as label,
        a.id as annotation_id
    from ranked_annotations a
    join task t on t.id = a.task_id
    join json_each(a.result) r
    where a.rn = 1
      and t.project_id = ?
      and json_extract(r.value, '$.from_name') = 'fur_color'
      and json_extract(r.value, '$.value.choices[0]') is not null
    order by original_split, label, filename
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(query, (PROJECT_ID, PROJECT_ID))]


def find_source_image(row):
    filename = row["filename"]
    source_label = row["fur_color_source"]
    if source_label:
        candidate = SOURCE_ROOT / source_label / filename
        if candidate.is_file():
            return candidate

    matches = list(SOURCE_ROOT.rglob(filename))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise RuntimeError(f"Multiple source matches for {filename}: {matches}")
    raise FileNotFoundError(filename)


def export_rows(rows):
    missing = []
    copied = 0
    split_counts = Counter()
    label_counts = Counter()
    split_label_counts = Counter()
    seen_destinations = set()

    for row in rows:
        split = row["original_split"] or "unsplit"
        label = row["label"]
        filename = row["filename"]
        destination = EXPORT_ROOT / split / label / filename

        if destination in seen_destinations:
            raise RuntimeError(f"Duplicate export destination: {destination}")
        seen_destinations.add(destination)

        try:
            source = find_source_image(row)
        except FileNotFoundError:
            missing.append(row)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

        copied += 1
        split_counts[split] += 1
        label_counts[label] += 1
        split_label_counts[(split, label)] += 1

    return {
        "copied": copied,
        "missing": missing,
        "split_counts": split_counts,
        "label_counts": label_counts,
        "split_label_counts": split_label_counts,
    }


def write_metadata(rows, export_result):
    label_map = {
        label: {
            "index": index,
            "display_name": LABEL_DISPLAY_NAMES[label],
        }
        for index, label in enumerate(LABEL_ORDER)
    }
    (EXPORT_ROOT / "label_map.json").write_text(
        json.dumps(label_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    split_summary = {}
    for (split, label), count in sorted(export_result["split_label_counts"].items()):
        split_summary.setdefault(split, {})[label] = count

    summary = {
        "project_id": PROJECT_ID,
        "source_project": "v9_yolo26_fur_color_cls",
        "source_root": str(SOURCE_ROOT),
        "export_root": str(EXPORT_ROOT),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "labeled_tasks": len(rows),
        "copied": export_result["copied"],
        "missing": len(export_result["missing"]),
        "split_counts": dict(sorted(export_result["split_counts"].items())),
        "label_counts": {
            label: export_result["label_counts"].get(label, 0)
            for label in LABEL_ORDER
        },
        "split_label_counts": split_summary,
    }
    (EXPORT_ROOT / "split_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if export_result["missing"]:
        (EXPORT_ROOT / "missing_files.json").write_text(
            json.dumps(export_result["missing"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main():
    if not DB_PATH.is_file():
        raise FileNotFoundError(DB_PATH)
    if not SOURCE_ROOT.is_dir():
        raise FileNotFoundError(SOURCE_ROOT)

    rows = fetch_labeled_tasks()
    prepare_export_root()
    export_result = export_rows(rows)
    write_metadata(rows, export_result)

    print(f"export_root={EXPORT_ROOT}")
    print(f"labeled_tasks={len(rows)}")
    print(f"copied={export_result['copied']}")
    print(f"missing={len(export_result['missing'])}")
    for split, count in sorted(export_result["split_counts"].items()):
        print(f"split_{split}={count}")


if __name__ == "__main__":
    main()
