import json
import re
import shutil
from pathlib import Path


OUTPUT_DIR = Path("D:/gogelila/data") / "\u89c6\u9891"
MANIFEST_PATH = OUTPUT_DIR / "download_manifest.json"
REJECTED_MANIFEST_PATH = OUTPUT_DIR / "download_manifest_rejected.json"
REJECTED_DIR = OUTPUT_DIR / "_rejected_not_eating_drinking"

BAD_RE = re.compile(
    r"\b(ai|aivideo|ai-generated|generated|amongus|review|unboxing|product|advertisement|ad|hoopo|sheba|hen|hens|mouse|rabbit|snail|talkingtom|stella|chewy|reveal)\b"
    r"|\b(eats|eating)\s+cars?\b"
    r"|best\s+cat\s+food\s+bowls?"
    r"|cat\s+food\s+bowl\s+review"
    r"|green\s+snake\s+toy"
    r"|toy\s+eat\s+from\s+the\s+bowl"
    r"|questions\s+eating\s+cats"
    r"|playing\s+with\s+cats"
    r"|how\s+to\s+feed"
    r"|refuse\s+to\s+eat"
    r"|no\s+food\s+looking\s+to\s+eat"
    r"|big\s+cats"
    r"|purrfect\s+meal"
    r"|talking\s+tom"
    r"|reaction.*after\s+eating"
    r"|judges\s+the\s+food"
    r"|\balf\b",
    re.I,
)


def load_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, list) else []


def save_list(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def unique_dest(path: Path) -> Path:
    candidate = REJECTED_DIR / path.name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 2
    while True:
        numbered = REJECTED_DIR / f"{stem}_rejected_{index}{suffix}"
        if not numbered.exists():
            return numbered
        index += 1


def item_file(item: dict) -> Path | None:
    raw = item.get("file")
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    fallback = OUTPUT_DIR / path.name
    return fallback if fallback.exists() else path


def main() -> int:
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_list(MANIFEST_PATH)
    rejected = load_list(REJECTED_MANIFEST_PATH)

    kept = []
    moved = []
    for item in manifest:
        title = item.get("title") or ""
        file_path = item_file(item)
        file_name = file_path.name if file_path else ""
        haystack = f"{title} {file_name}"
        if not BAD_RE.search(haystack):
            kept.append(item)
            continue

        updated = dict(item)
        updated["rejected_reason"] = "title or filename indicates non eating/drinking cat clip"
        if file_path and file_path.exists():
            dest = unique_dest(file_path)
            shutil.move(str(file_path), str(dest))
            updated["file"] = str(dest)
        rejected.append(updated)
        moved.append(file_name or title)

    save_list(MANIFEST_PATH, kept)
    save_list(REJECTED_MANIFEST_PATH, rejected)
    print(f"kept={len(kept)} moved_to_rejected={len(moved)}")
    for name in moved[:50]:
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
