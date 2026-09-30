import json
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(r"D:\gogelila\data\视频")
MANIFEST_PATH = OUTPUT_DIR / "download_manifest.json"
REJECTED_MANIFEST_PATH = OUTPUT_DIR / "download_manifest_rejected.json"
FAILED_MANIFEST_PATH = OUTPUT_DIR / "download_manifest_failed.json"
TARGET_COUNT = 420
MAX_DURATION_SECONDS = 60.0
MIN_DURATION_SECONDS = 1.0
SEARCH_LIMIT_PER_QUERY = 180

TIKTOK_URLS = [
    "https://www.tiktok.com/tag/cateating",
    "https://www.tiktok.com/tag/catseating",
    "https://www.tiktok.com/tag/cateatingfood",
    "https://www.tiktok.com/tag/kitteneating",
    "https://www.tiktok.com/tag/catdrinking",
    "https://www.tiktok.com/tag/catdrinkingwater",
    "https://www.tiktok.com/search/video?q=cat%20eating",
    "https://www.tiktok.com/search/video?q=cat%20eating%20food",
    "https://www.tiktok.com/search/video?q=kitten%20eating",
    "https://www.tiktok.com/search/video?q=cat%20drinking%20water",
]

YOUTUBE_QUERIES = [
    "cat eating shorts",
    "cat eating food shorts",
    "cat eats food shorts",
    "cats eating shorts",
    "cats eating food shorts",
    "kitten eating shorts",
    "kitten eating food shorts",
    "kitty eating food shorts",
    "cat eating wet food shorts",
    "cat eating dry food shorts",
    "cat eating kibble shorts",
    "cat eating cat food shorts",
    "cat eating food from bowl shorts",
    "cat eating from bowl shorts",
    "cat eating from food bowl shorts",
    "cat food bowl eating cat shorts",
    "cat eating canned food shorts",
    "cat eating pate shorts",
    "cat eating treats shorts",
    "cat eating breakfast shorts",
    "cat eating dinner shorts",
    "cat eating meal shorts",
    "hungry cat eating food shorts",
    "cat eats wet food shorts",
    "cat eats dry food shorts",
    "cat eats kibble shorts",
    "cat eats treats shorts",
    "kitten eating from bowl shorts",
    "kitten eating wet food shorts",
    "kitten eating dry food shorts",
    "kittens eating from bowl shorts",
    "cats eating from bowl shorts",
    "cats eating wet food shorts",
    "cats eating dry food shorts",
    "cat mukbang shorts",
    "cat asmr eating shorts",
    "kitten asmr eating shorts",
    "cat eating asmr wet food shorts",
    "cat eating asmr dry food shorts",
    "cat eating asmr from bowl shorts",
    "cat chewing food shorts",
    "crunchy cat eating shorts",
    "cat crunching food shorts",
    "cat munching food shorts",
    "cat drinking water shorts",
    "cat drinks water shorts",
    "kitten drinking water shorts",
    "kitten drinking milk shorts",
    "cat drinking milk shorts",
    "cat drinking from bowl shorts",
    "cat drinking from water bowl shorts",
    "cat drinking water from bowl shorts",
    "cat drinking fountain shorts",
    "cat drinking from fountain shorts",
    "cat licking water shorts",
    "cat licking milk shorts",
    "cat lapping water shorts",
    "cat water bowl drinking shorts",
    "kitten drinking from bowl shorts",
    "kitten drinking water bowl shorts",
    "cats drinking water shorts",
    "cats drinking from bowl shorts",
    "cat eating and drinking shorts",
    "feeding cats shorts",
    "feeding kitten shorts",
    "feeding kittens shorts",
    "cat feeding time shorts",
    "cats feeding time shorts",
    "stray cat eating food shorts",
    "stray kitten eating food shorts",
    "stray cats eating food shorts",
    "stray cats drinking water shorts",
    "猫咪 吃饭 shorts",
    "猫咪 喝水 shorts",
    "小猫 吃饭 shorts",
    "小猫 喝奶 shorts",
]

CAT_RE = re.compile(r"\b(cat|cats|kitten|kittens|kitty|feline)\b|猫|小猫|猫咪", re.I)
ACTION_RE = re.compile(
    r"\b(eat|eats|eating|feed|feeds|feeding|fed|meal|kibble|chew|chews|chewing|crunch|crunches|crunching|munch|munches|munching|drink|drinks|drinking|lapping)\b|吃|吃饭|喂|喝|喝水|喝奶",
    re.I,
)
BAD_RE = re.compile(
    r"\b(dog|puppy|bird|hawk|hen|hens|pig|cow|horse|bear|monkey|mouse|rabbit|snail|cartoon|animation|animated|anime|minecraft|roblox|gameplay|talkingtom|talking\s+tom|aivideo|ai-generated|generated|ai|amongus|review|unboxing|product|advertisement|ad|stella|chewy|reveal)\b|\b(eats|eating)\s+cars?\b|best\s+cat\s+food\s+bowls?|cat\s+food\s+bowl\s+review|green\s+snake\s+toy|toy\s+eat\s+from\s+the\s+bowl|playing\s+with\s+cats|how\s+to\s+feed|refuse\s+to\s+eat|no\s+food\s+looking\s+to\s+eat|big\s+cats|purrfect\s+meal|hoopo|sheba|questions\s+eating\s+cats|动画|卡通|游戏",
    re.I,
)
PAW_GROOMING_RE = re.compile(r"\b(lick|licks|licking)\b[^.!?]{0,30}\b(paw|paws|fur|body|tail)\b", re.I)
LICK_DRINK_RE = re.compile(
    r"\b(lick|licks|licking)\b[^.!?]{0,40}\b(water|milk|cream|glass|bowl|fountain)\b",
    re.I,
)


def log(message: str) -> None:
    print(message, flush=True)


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))


def load_json_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, list) else []


def save_manifest(items: list[dict]) -> None:
    MANIFEST_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def existing_source_urls(items: list[dict]) -> set[str]:
    return {item["source_url"] for item in items if item.get("source_url")}


def rejected_source_urls() -> set[str]:
    return existing_source_urls(load_json_list(REJECTED_MANIFEST_PATH))


def failed_source_urls() -> set[str]:
    return existing_source_urls(load_json_list(FAILED_MANIFEST_PATH))


def entry_source_url(entry: dict) -> str | None:
    return entry.get("webpage_url") or entry.get("url")


def append_failed(entry: dict, query: str, reason: str, source: str = "youtube") -> None:
    failed = load_json_list(FAILED_MANIFEST_PATH)
    failed.append(
        {
            "source": source,
            "source_url": entry_source_url(entry),
            "query": query,
            "title": entry.get("title"),
            "duration_seconds": entry.get("duration"),
            "failed_reason": reason,
        }
    )
    FAILED_MANIFEST_PATH.write_text(json.dumps(failed, indent=2, ensure_ascii=False), encoding="utf-8")



def next_index(manifest: list[dict]) -> int:
    indexes = []
    for item in manifest:
        file = item.get("file")
        if file:
            match = re.match(r"(\d+)_", Path(file).name)
            if match:
                indexes.append(int(match.group(1)))
    for file in OUTPUT_DIR.glob("*.*"):
        match = re.match(r"(\d+)_", file.name)
        if match:
            indexes.append(int(match.group(1)))
    return max(indexes, default=0) + 1


def clean_title(title: str) -> str:
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    return title[:70] or "cat_video"


def contexts_from(text: str) -> list[str]:
    contexts = []
    if re.search(r"\b(eat|eats|eating|feed|feeds|feeding|food|meal|kibble|chew|chewing|crunch|crunching|munch|munching)\b|吃|吃饭|喂", text, re.I):
        contexts.append("eating")
    if re.search(r"\b(drink|drinks|drinking|water|milk|lapping)\b|喝|喝水|喝奶", text, re.I):
        contexts.append("drinking")
    if re.search(r"\bbowl\b|碗", text, re.I):
        contexts.append("bowl")
    return contexts or ["cat"]


def youtube_search(query: str, limit: int) -> list[dict]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--flat-playlist",
        "-J",
        f"ytsearch{limit}:{query}",
    ]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        log(f"youtube search failed: {query}: {proc.stderr.strip()[:300]}")
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log(f"youtube search JSON failed: {query}")
        return []
    entries = [entry for entry in data.get("entries") or [] if entry]
    log(f"youtube search: {query} entries={len(entries)}")
    return entries


def tiktok_search(url: str) -> list[dict]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--skip-download",
        "--flat-playlist",
        "-J",
        url,
    ]
    proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        log(f"tiktok search failed: {url}: {proc.stderr.strip()[:300]}")
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log(f"tiktok search JSON failed: {url}")
        return []
    entries = [entry for entry in data.get("entries") or [] if entry]
    if not entries and data.get("webpage_url"):
        entries = [data]
    log(f"tiktok search: {url} entries={len(entries)}")
    return entries


def is_relevant_entry(entry: dict, query: str) -> tuple[bool, str]:
    title = entry.get("title") or ""
    description = entry.get("description") or ""
    channel = entry.get("channel") or entry.get("uploader") or ""
    text = f"{title} {description} {channel}"

    if BAD_RE.search(text):
        return False, "bad subject"
    if PAW_GROOMING_RE.search(text):
        return False, "grooming not drinking"
    if not CAT_RE.search(text):
        return False, "no cat context"
    if not ACTION_RE.search(title) and not LICK_DRINK_RE.search(title):
        return False, "title lacks eating/drinking context"
    if not ACTION_RE.search(text) and not LICK_DRINK_RE.search(text):
        return False, "no eating/drinking context"

    duration = entry.get("duration")
    if duration is None:
        return False, "unknown duration"
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return False, "bad duration"
    if duration < MIN_DURATION_SECONDS:
        return False, "too short"
    if duration > MAX_DURATION_SECONDS:
        return False, f"too long: {duration}"
    return True, "ok"


def validate_duration(file: Path) -> float | None:
    capture = cv2.VideoCapture(str(file))
    if not capture.isOpened():
        return None
    fps = capture.get(cv2.CAP_PROP_FPS)
    frames = capture.get(cv2.CAP_PROP_FRAME_COUNT)
    capture.release()
    if not fps or fps <= 0 or not frames or frames <= 0:
        return None
    return float(frames / fps)


def download_video(entry: dict, query: str, manifest: list[dict], index: int, source: str) -> dict | None:
    source_url = entry_source_url(entry)
    if not source_url:
        return None
    title = entry.get("title") or source_url
    contexts = contexts_from(f"{title} {entry.get('description') or ''} {query}")
    filename = f"{index:04d}_{source}_{'_'.join(contexts)}_{clean_title(title)}.%(ext)s"
    template = OUTPUT_DIR / filename
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-mtime",
        "--restrict-filenames",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "20",
        "--merge-output-format",
        "mp4",
        "--match-filter",
        "duration <= 60",
        "-f",
        "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "-o",
        str(template),
        source_url,
    ]
    proc = subprocess.run(command, cwd=ROOT)
    if proc.returncode != 0:
        log(f"download failed: {source_url}")
        return None

    matches = sorted(
        OUTPUT_DIR.glob(filename.replace("%(ext)s", "*")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not matches:
        log(f"download missing: {source_url}")
        return None
    dest = matches[0]
    if not dest.exists() or dest.stat().st_size == 0:
        log(f"download empty: {source_url}")
        return None

    actual_duration = validate_duration(dest)
    if actual_duration is not None and actual_duration > MAX_DURATION_SECONDS + 0.5:
        log(f"delete too-long downloaded file: {dest.name} duration={actual_duration:.1f}")
        dest.unlink(missing_ok=True)
        return None

    item = {
        "file": str(dest),
        "source": source,
        "source_url": source_url,
        "query": query,
        "title": title,
        "description": entry.get("description"),
        "contexts": contexts,
        "duration_seconds": entry.get("duration"),
        "validated_duration_seconds": actual_duration,
        "license_note": f"{source} video; review source permissions before redistribution.",
    }
    manifest.append(item)
    save_manifest(manifest)
    log(f"saved {len(manifest)}/{TARGET_COUNT}: {dest.name}")
    return item


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    sources_seen = existing_source_urls(manifest) | rejected_source_urls() | failed_source_urls()
    index = next_index(manifest)
    log(f"existing downloaded entries: {len(manifest)}")

    skipped = 0
    for url in TIKTOK_URLS:
        if len(manifest) >= TARGET_COUNT:
            break
        entries = tiktok_search(url)
        for entry in entries:
            if len(manifest) >= TARGET_COUNT:
                break
            source_url = entry_source_url(entry)
            if not source_url or source_url in sources_seen:
                continue
            ok, reason = is_relevant_entry(entry, url)
            if not ok:
                skipped += 1
                if skipped % 50 == 0:
                    log(f"skipped {skipped}; latest reason={reason}")
                continue
            result = download_video(entry, url, manifest, index, "tiktok")
            sources_seen.add(source_url)
            if result:
                index += 1
            else:
                append_failed(entry, url, "download failed", "tiktok")
                skipped += 1
            time.sleep(0.2)

    for query in YOUTUBE_QUERIES:
        if len(manifest) >= TARGET_COUNT:
            break
        entries = youtube_search(query, SEARCH_LIMIT_PER_QUERY)
        for entry in entries:
            if len(manifest) >= TARGET_COUNT:
                break
            source_url = entry_source_url(entry)
            if not source_url or source_url in sources_seen:
                continue
            ok, reason = is_relevant_entry(entry, query)
            if not ok:
                skipped += 1
                if skipped % 50 == 0:
                    log(f"skipped {skipped}; latest reason={reason}")
                continue
            result = download_video(entry, query, manifest, index, "youtube")
            sources_seen.add(source_url)
            if result:
                index += 1
            else:
                append_failed(entry, query, "download failed", "youtube")
                skipped += 1
            time.sleep(0.2)

    log(f"final downloaded entries: {len(manifest)}")
    log(f"skipped candidates: {skipped}")
    log(f"manifest: {MANIFEST_PATH}")
    return 0 if len(manifest) >= min(TARGET_COUNT, 1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
