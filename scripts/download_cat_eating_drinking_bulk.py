import json
import math
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, urlsplit

import cv2
from curl_cffi import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = Path(r"D:\gogelila\data\视频")
MANIFEST_PATH = OUTPUT_DIR / "download_manifest.json"
REJECTED_MANIFEST_PATH = OUTPUT_DIR / "download_manifest_rejected.json"
TARGET_COUNT = 420
MAX_DURATION_SECONDS = 60.0
MIN_DURATION_SECONDS = 1.0
METADATA_WORKERS = 8

PEXELS_QUERIES = [
    "cat eating",
    "cat eating food",
    "cat eating bowl",
    "cat eating from bowl",
    "cat eating kibble",
    "cat feeding",
    "cats eating",
    "cats eating together",
    "kitten eating",
    "kitten feeding",
    "cat drinking",
    "cat drinking water",
    "cat drinking bowl",
    "cat drinking milk",
    "cat water bowl",
    "cat milk bowl",
    "kitten drinking",
    "kitten drinking water",
    "kitten drinking bowl",
    "cat licking milk",
    "cat licking water",
    "cat food bowl",
    "feeding cats",
]

PIXABAY_QUERIES = [
    "cat eating",
    "cat eating food",
    "cat eating bowl",
    "cat food bowl",
    "cat drinking",
    "cat drinking water",
    "cat drinking milk",
    "kitten eating",
    "kitten drinking",
]

COMMONS_QUERIES = [
    "cat eating",
    "cat drinking",
    "cat lapping water",
]

CAT_RE = re.compile(r"\b(cat|cats|kitten|kittens|feline)\b", re.I)
ACTION_RE = re.compile(
    r"\b(eat|eats|eating|feed|feeding|fed|drink|drinks|drinking|kibble|meal|chew|chews|chewing|crunch|crunches|crunching|munch|munches|munching|lapping)\b",
    re.I,
)
STRONG_ACTION_RE = re.compile(
    r"\b(eat|eats|eating|feed|feeding|drink|drinks|drinking|chew|chews|chewing|crunch|crunches|crunching|munch|munches|munching|lapping)\b",
    re.I,
)
CAT_ACTION_RE = re.compile(
    r"\b(cat|cats|kitten|kittens|feline)\b[^.!?]{0,90}\b(eat|eats|eating|feed|feeding|drink|drinks|drinking|kibble|meal|chew|chews|chewing|crunch|crunches|crunching|munch|munches|munching|lapping)\b",
    re.I,
)
FEEDING_CAT_RE = re.compile(
    r"\b(feed|feeds|feeding|fed)\b[^.!?]{0,70}\b(cat|cats|kitten|kittens|feline)\b",
    re.I,
)
CAT_LICKING_DRINK_RE = re.compile(
    r"\b(cat|cats|kitten|kittens|feline)\b[^.!?]{0,90}\b(lick|licks|licking)\b[^.!?]{0,40}\b(water|milk|cream|glass|bowl|fountain)\b",
    re.I,
)
LIKELY_BAD_RE = re.compile(
    r"\b(wasp|bee|beetle|insect|bird|boar|dog|puppy|panda|bear|horse|cow|pig|monkey|cartoon|animation|animated|anime|minecraft|roblox)\b",
    re.I,
)


def log(message: str) -> None:
    print(message, flush=True)


def fetch(url: str) -> str:
    response = requests.get(
        url,
        impersonate="chrome136",
        timeout=35,
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    response.raise_for_status()
    return response.text


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))


def save_manifest(items: list[dict]) -> None:
    MANIFEST_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def existing_source_urls(items: list[dict]) -> set[str]:
    return {item["source_url"] for item in items if item.get("source_url")}


def rejected_source_urls() -> set[str]:
    if not REJECTED_MANIFEST_PATH.exists():
        return set()
    rejected = json.loads(REJECTED_MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    return existing_source_urls(rejected if isinstance(rejected, list) else [])


def pexels_links(query: str, max_pages: int = 25) -> list[str]:
    links: list[str] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = f"https://www.pexels.com/search/videos/{quote(query)}/"
        if page > 1:
            url += f"?page={page}"
        try:
            html = fetch(url)
        except Exception as exc:
            log(f"pexels search failed: {query} page {page}: {exc}")
            if "404" in str(exc):
                break
            continue

        page_links = set(re.findall(r"https://www\.pexels\.com/video/[^\"'?<>]+?-\d+/", html))
        page_links.update(f"https://www.pexels.com{match}" for match in re.findall(r"/video/[^\"'?<>]+?-\d+/", html))
        new_links = [link for link in sorted(page_links) if link not in seen]
        for link in new_links:
            seen.add(link)
            links.append(link)
        log(f"pexels search: {query} page {page} new={len(new_links)} total_query={len(links)}")
        if len(new_links) == 0:
            break
        time.sleep(0.25)
    return links


def pixabay_links(query: str, max_pages: int = 8) -> list[str]:
    links: list[str] = []
    seen = set()
    for page in range(1, max_pages + 1):
        url = f"https://pixabay.com/videos/search/{quote(query)}/"
        if page > 1:
            url += f"?pagi={page}"
        try:
            html = fetch(url)
        except Exception as exc:
            log(f"pixabay search failed: {query} page {page}: {exc}")
            continue
        page_links = set(f"https://pixabay.com{match}" for match in re.findall(r"/videos/[^\"<>]+?-\d+/", html))
        new_links = [link for link in sorted(page_links) if link not in seen]
        for link in new_links:
            seen.add(link)
            links.append(link)
        log(f"pixabay search: {query} page {page} new={len(new_links)} total_query={len(links)}")
        if len(new_links) == 0:
            break
        time.sleep(0.25)
    return links


def commons_links(query: str, limit: int = 50) -> list[str]:
    url = (
        "https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={quote(query + ' filetype:video')}"
        f"&gsrnamespace=6&gsrlimit={limit}&prop=imageinfo&iiprop=url|mime|size|extmetadata&format=json&origin=*"
    )
    try:
        data = requests.get(url, impersonate="chrome136", timeout=35).json()
    except Exception as exc:
        log(f"commons search failed: {query}: {exc}")
        return []
    pages = data.get("query", {}).get("pages", {})
    links = []
    for page in pages.values():
        info = page.get("imageinfo", [{}])[0]
        media_url = info.get("url")
        if media_url and (info.get("mime", "").startswith("video/") or media_url.lower().endswith((".ogv", ".webm", ".mp4"))):
            links.append(media_url)
    log(f"commons search: {query} links={len(links)}")
    return links


def collect_candidates() -> list[dict]:
    candidates: list[dict] = []
    seen = set()

    def add(source: str, url: str, query: str, rank: int) -> None:
        if url in seen:
            return
        seen.add(url)
        candidates.append({"source": source, "source_url": url, "query": query, "rank": rank})

    rank = 0
    for query in PEXELS_QUERIES:
        for url in pexels_links(query):
            add("pexels", url, query, rank)
            rank += 1

    for query in PIXABAY_QUERIES:
        for url in pixabay_links(query):
            add("pixabay", url, query, rank + 100000)
            rank += 1

    for query in COMMONS_QUERIES:
        for url in commons_links(query):
            add("commons", url, query, rank + 200000)
            rank += 1

    log(f"total unique candidates: {len(candidates)}")
    return candidates


def process_candidates(
    candidates: list[dict],
    manifest: list[dict],
    sources_seen: set[str],
    start_index: int,
    skipped: int,
) -> tuple[int, int]:
    index = start_index
    pending = [candidate for candidate in candidates if candidate["source_url"] not in sources_seen]
    if not pending:
        return index, skipped

    accepted: list[tuple[dict, dict, list[str]]] = []
    log(f"metadata inspect: pending={len(pending)} workers={METADATA_WORKERS}")
    with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as executor:
        futures = [executor.submit(inspect_candidate, candidate) for candidate in pending]
        for future in as_completed(futures):
            candidate, info, contexts, reason = future.result()
            if info is None:
                skipped += 1
                if skipped % 50 == 0:
                    log(f"skipped {skipped}; latest reason={reason}")
                continue
            accepted.append((candidate, info, contexts))
            if len(accepted) % 25 == 0:
                log(f"accepted metadata in batch: {len(accepted)}")

    accepted.sort(key=lambda item: item[0].get("rank", 0))
    log(f"metadata accepted: {len(accepted)} of {len(pending)}")

    for candidate, info, contexts in accepted:
        if len(manifest) >= TARGET_COUNT:
            break
        result = download_item(candidate, info, manifest, index, contexts)
        sources_seen.add(candidate["source_url"])
        if result:
            index += 1
        else:
            skipped += 1
        time.sleep(0.2)
    return index, skipped


def yt_dlp_info(url: str, source: str) -> dict | None:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--skip-download",
        "-j",
    ]
    if source in {"pexels", "pixabay"}:
        command.extend(["--impersonate", "chrome-136:macos-15"])
    command.append(url)

    for attempt in range(1, 4):
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode == 0:
            lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
            if not lines:
                return None
            try:
                return json.loads(lines[-1])
            except json.JSONDecodeError:
                return None
        if attempt < 3:
            time.sleep(1.5 * attempt)
    log(f"metadata failed: {url}")
    return None


def inspect_candidate(candidate: dict) -> tuple[dict, dict | None, list[str], str]:
    info = yt_dlp_info(candidate["source_url"], candidate["source"])
    if not info:
        return candidate, None, [], "metadata failed"
    ok, contexts, reason = is_relevant(info)
    if not ok:
        return candidate, None, [], reason
    return candidate, info, contexts, "ok"


def contexts_from(text: str) -> list[str]:
    contexts = []
    for key, pattern in {
        "eating": r"\b(eat|eats|eating|feed|feeding|kibble|meal|chew|chews|chewing|crunch|crunches|crunching|munch|munches|munching)\b",
        "drinking": r"\b(drink|drinks|drinking|lapping)\b|\b(lick|licks|licking)\b[^.!?]{0,40}\b(water|milk|cream|glass|bowl|fountain)\b",
        "bowl": r"\bbowl\b",
    }.items():
        if re.search(pattern, text, re.I):
            contexts.append(key)
    return contexts


def has_cat_action_context(text: str) -> bool:
    return bool(
        CAT_ACTION_RE.search(text)
        or FEEDING_CAT_RE.search(text)
        or CAT_LICKING_DRINK_RE.search(text)
    )


def is_relevant(info: dict) -> tuple[bool, list[str], str]:
    title = info.get("title") or ""
    description = info.get("description") or ""
    tags = " ".join(info.get("tags") or [])
    uploader = info.get("uploader") or ""
    channel = info.get("channel") or ""
    visual_text = f"{title} {description}".lower()
    all_text = f"{visual_text} {tags} {uploader} {channel}".lower()

    if not CAT_RE.search(all_text):
        return False, [], "no cat context"
    if not ACTION_RE.search(all_text):
        return False, [], "no eating/drinking action"
    if not has_cat_action_context(all_text):
        return False, [], "no cat eating/drinking phrase"

    # Avoid obvious non-cat false positives such as "wasp eating cat food".
    if LIKELY_BAD_RE.search(title):
        return False, [], "likely non-cat subject"

    duration = info.get("duration")
    if duration is None:
        return False, [], "unknown duration"
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return False, [], "bad duration"
    if duration < MIN_DURATION_SECONDS:
        return False, [], "too short"
    if duration > MAX_DURATION_SECONDS:
        return False, [], f"too long: {duration}"

    contexts = contexts_from(all_text)
    if not contexts:
        return False, [], "no contexts"
    return True, contexts, "ok"


def clean_title(title: str) -> str:
    title = re.sub(r"Free Stock Video.*", "", title, flags=re.I).strip()
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    return title[:70] or "cat_video"


def next_index(manifest: list[dict]) -> int:
    indexes = []
    for item in manifest:
        file = item.get("file")
        if not file:
            continue
        match = re.match(r"(\d+)_", Path(file).name)
        if match:
            indexes.append(int(match.group(1)))
    for file in OUTPUT_DIR.glob("*.*"):
        match = re.match(r"(\d+)_", file.name)
        if match:
            indexes.append(int(match.group(1)))
    return max(indexes, default=0) + 1


def direct_url(info: dict) -> str | None:
    url = info.get("url")
    if url:
        return urlsplit(url).geturl().split("#", 1)[0]
    formats = info.get("formats") or []
    for fmt in reversed(formats):
        url = fmt.get("url")
        if url:
            return urlsplit(url).geturl().split("#", 1)[0]
    return None


def download_direct(url: str, dest: Path, referer: str | None) -> bool:
    headers = {"Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    for attempt in range(1, 4):
        response = None
        try:
            response = requests.get(url, impersonate="chrome136", timeout=90, stream=True, headers=headers)
            response.raise_for_status()
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
            tmp.replace(dest)
            return True
        except Exception as exc:
            log(f"direct download failed attempt {attempt}: {exc}")
            time.sleep(1.5 * attempt)
        finally:
            if response is not None:
                response.close()
    return False


def download_with_ytdlp(url: str, dest_template: Path, source: str) -> bool:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--no-mtime",
        "--restrict-filenames",
        "--retries",
        "5",
        "-o",
        str(dest_template),
    ]
    if source in {"pexels", "pixabay"}:
        command.extend(["--impersonate", "chrome-136:macos-15"])
    command.append(url)
    proc = subprocess.run(command, cwd=ROOT)
    return proc.returncode == 0


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


def download_item(candidate: dict, info: dict, manifest: list[dict], index: int, contexts: list[str]) -> dict | None:
    source = candidate["source"]
    title = info.get("title") or candidate["source_url"]
    ext = (info.get("ext") or "mp4").lower()
    if ext not in {"mp4", "webm", "ogv", "mov"}:
        ext = "mp4"
    filename = f"{index:04d}_{source}_{'_'.join(contexts)}_{clean_title(title)}.{ext}"
    dest = OUTPUT_DIR / filename

    success = False
    template = dest.with_suffix(".%(ext)s")
    success = download_with_ytdlp(candidate["source_url"], template, source)
    matches = sorted(OUTPUT_DIR.glob(dest.with_suffix(".*").name), key=lambda path: path.stat().st_mtime, reverse=True)
    if matches:
        dest = matches[0]
    if not success or not dest.exists() or dest.stat().st_size == 0:
        log(f"download failed: {candidate['source_url']}")
        return None

    actual_duration = validate_duration(dest)
    if actual_duration is not None and actual_duration > MAX_DURATION_SECONDS + 0.5:
        log(f"delete too-long downloaded file: {dest.name} duration={actual_duration:.1f}")
        dest.unlink(missing_ok=True)
        return None

    item = {
        "file": str(dest),
        "source": source,
        "source_url": candidate["source_url"],
        "query": candidate["query"],
        "title": title,
        "description": info.get("description"),
        "contexts": contexts,
        "duration_seconds": info.get("duration"),
        "validated_duration_seconds": actual_duration,
        "license_note": {
            "pexels": "Pexels free stock video; review current Pexels license before redistribution.",
            "pixabay": "Pixabay free stock video; review current Pixabay license before redistribution.",
            "commons": "Wikimedia Commons media; review each file page for license and attribution.",
        }.get(source, "Review source license before redistribution."),
    }
    manifest.append(item)
    save_manifest(manifest)
    log(f"saved {len(manifest)}/{TARGET_COUNT}: {dest.name}")
    return item


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    sources_seen = existing_source_urls(manifest) | rejected_source_urls()
    log(f"existing downloaded entries: {len(manifest)}")

    index = next_index(manifest)
    skipped = 0
    candidate_seen: set[str] = set()

    rank = 0
    for query in PEXELS_QUERIES:
        if len(manifest) >= TARGET_COUNT:
            break
        batch = []
        for url in pexels_links(query):
            if url in candidate_seen:
                continue
            candidate_seen.add(url)
            batch.append({"source": "pexels", "source_url": url, "query": query, "rank": rank})
            rank += 1
        log(f"processing pexels batch: {query} candidates={len(batch)} downloaded={len(manifest)}")
        index, skipped = process_candidates(batch, manifest, sources_seen, index, skipped)

    for query in PIXABAY_QUERIES:
        if len(manifest) >= TARGET_COUNT:
            break
        batch = []
        for url in pixabay_links(query):
            if url in candidate_seen:
                continue
            candidate_seen.add(url)
            batch.append({"source": "pixabay", "source_url": url, "query": query, "rank": rank + 100000})
            rank += 1
        log(f"processing pixabay batch: {query} candidates={len(batch)} downloaded={len(manifest)}")
        index, skipped = process_candidates(batch, manifest, sources_seen, index, skipped)

    for query in COMMONS_QUERIES:
        if len(manifest) >= TARGET_COUNT:
            break
        batch = []
        for url in commons_links(query):
            if url in candidate_seen:
                continue
            candidate_seen.add(url)
            batch.append({"source": "commons", "source_url": url, "query": query, "rank": rank + 200000})
            rank += 1
        log(f"processing commons batch: {query} candidates={len(batch)} downloaded={len(manifest)}")
        index, skipped = process_candidates(batch, manifest, sources_seen, index, skipped)

    log(f"final downloaded entries: {len(manifest)}")
    log(f"skipped candidates: {skipped}")
    log(f"manifest: {MANIFEST_PATH}")
    return 0 if len(manifest) >= min(TARGET_COUNT, 1) else 1


if __name__ == "__main__":
    raise SystemExit(main())
