import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from curl_cffi import requests


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "cat_video_assets"
MANIFEST = OUT_DIR / "manifest.json"

SEARCH_QUERIES = [
    "cat eating bowl",
    "cat eating from bowl",
    "cat eating from food bowl",
    "cat eating from metal bowl",
    "cat eating food",
    "cat eating kibble",
    "cat eating meal",
    "cats eating together",
    "cats eating bowl",
    "cat drinking bowl",
    "cat drinking from bowl",
    "cat food bowl",
    "cat water bowl",
    "cat milk bowl",
    "cat feeding",
    "cat feeding bowl",
    "feeding cats",
    "kitten eating",
    "kitten drinking bowl",
    "kitten feeding",
    "cat eating",
    "cat drinking water",
    "cat litter box",
    "cat toilet",
    "cat in litter box",
    "kitty litter cat",
    "cat indoors",
    "house cat",
    "domestic cat",
    "kitten",
    "cat pet",
    "stray cat",
    "cute cat",
]

CONTEXT_KEYWORDS = (
    "bowl",
    "food",
    "feeding",
    "eating",
    "drink",
    "water",
    "meal",
)

CAT_KEYWORDS = ("cat", "kitten", "feline")
STRICT_FILLER_KEYWORDS = (
    "cat",
    "cats",
    "kitten",
    "kittens",
    "feline",
    "domestic",
    "stray",
)

TARGET_COUNT = 50


def log(message: str) -> None:
    print(message, flush=True)


def fetch(url: str) -> str:
    response = requests.get(
        url,
        impersonate="chrome136",
        timeout=30,
        headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    response.raise_for_status()
    return response.text


def pexels_video_links(query: str) -> list[str]:
    html = fetch(f"https://www.pexels.com/search/videos/{quote(query)}/")
    links = set()
    for match in re.findall(r"https://www\.pexels\.com/video/[^\"'?<>]+?-\d+/", html):
        links.add(match)
    for match in re.findall(r"/video/[^\"'?<>]+?-\d+/", html):
        links.add(f"https://www.pexels.com{match}")
    return sorted(links)


def info_for(url: str) -> dict | None:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--impersonate",
        "chrome-136:macos-15",
        "--no-playlist",
        "--skip-download",
        "-j",
        url,
    ]
    proc = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
    if proc.returncode != 0:
        log(f"skip metadata: {url} :: {proc.stderr.strip()[:240]}")
        return None

    lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        return None

    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def classify(info: dict) -> tuple[bool, list[str]]:
    visual_text_parts = [
        info.get("title") or "",
        info.get("description") or "",
    ]
    visual_text = " ".join(visual_text_parts).lower()
    text_parts = [
        info.get("title") or "",
        info.get("description") or "",
        " ".join(info.get("tags") or []),
    ]
    text = " ".join(text_parts).lower()
    has_cat = any(word in visual_text for word in CAT_KEYWORDS)
    contexts = [word for word in CONTEXT_KEYWORDS if word in text]
    if any(phrase in text for phrase in ("litter box", "cat toilet", "kitty litter")):
        contexts.append("litter_box")
    return has_cat, contexts


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def safe_name(index: int, info: dict, contexts: list[str]) -> str:
    title = info.get("title") or info.get("id") or f"cat_video_{index:02d}"
    title = re.sub(r"Free Stock Video.*", "", title, flags=re.I).strip()
    title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    title = title[:58] or f"cat_video_{index:02d}"
    ctx = "_".join(contexts[:3]) or "cat"
    return f"{index:02d}_{ctx}_{title}.%(ext)s"


def next_file_index() -> int:
    prefixes = []
    for file in OUT_DIR.glob("*.mp4"):
        match = re.match(r"(\d+)_", file.name)
        if match:
            prefixes.append(int(match.group(1)))
    return max(prefixes, default=0) + 1


def download(url: str, output_template: str) -> bool:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--impersonate",
        "chrome-136:macos-15",
        "--no-playlist",
        "--no-mtime",
        "--restrict-filenames",
        "-o",
        str(OUT_DIR / output_template),
        url,
    ]
    proc = subprocess.run(command, cwd=ROOT)
    return proc.returncode == 0


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    existing_manifest = load_manifest()
    existing_urls = {item.get("source_url") for item in existing_manifest if item.get("source_url")}
    existing_files = [item for item in existing_manifest if item.get("file")]
    next_index = next_file_index()
    remaining = max(TARGET_COUNT - len(existing_files), 0)

    if remaining == 0:
        log(f"already have {len(existing_files)} videos")
        return 0

    candidates: list[str] = []
    seen = set()
    for query in SEARCH_QUERIES:
        log(f"search: {query}")
        try:
            links = pexels_video_links(query)
        except Exception as exc:
            log(f"search failed: {query} :: {exc}")
            continue

        for link in links:
            if link not in seen:
                seen.add(link)
                candidates.append(link)

        log(f"  found {len(links)} links, total {len(candidates)}")
        time.sleep(1.0)

    picked: list[dict] = []
    metadata_goal = max(remaining * 10, 40)
    for url in candidates:
        if len(picked) >= metadata_goal:
            break
        if url in existing_urls:
            continue

        info = info_for(url)
        if not info:
            continue

        has_cat, contexts = classify(info)
        if not has_cat:
            continue

        text = " ".join(
            [
                info.get("title") or "",
                info.get("description") or "",
                " ".join(info.get("tags") or []),
            ]
        ).lower()
        strict_terms = [word for word in STRICT_FILLER_KEYWORDS if word in text]

        priority = len(contexts) * 10 + len(strict_terms)
        picked.append({"priority": priority, "contexts": contexts, "info": info, "url": url})
        log(f"pick: {url} :: {contexts or ['cat']}")
        time.sleep(0.5)

    picked.sort(key=lambda item: (item["priority"], len(item["contexts"])), reverse=True)

    manifest = list(existing_manifest)
    for item in picked:
        if len(manifest) >= TARGET_COUNT:
            break

        index = next_index
        next_index += 1
        info = item["info"]
        template = safe_name(index, info, item["contexts"])
        log(f"download {index}/{TARGET_COUNT}: {info.get('title')}")
        if not download(item["url"], template):
            log(f"download failed: {item['url']}")
            continue

        files = sorted(OUT_DIR.glob(template.replace("%(ext)s", "*")), key=lambda p: p.stat().st_mtime, reverse=True)
        local_file = files[0] if files else None
        manifest.append(
            {
                "file": str(local_file) if local_file else "",
                "title": info.get("title"),
                "description": info.get("description"),
                "contexts": item["contexts"],
                "source_url": item["url"],
                "direct_url": info.get("url"),
                "duration_seconds": info.get("duration"),
                "license_note": "Pexels free stock video; review current Pexels license before redistribution.",
            }
        )

    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"wrote {MANIFEST}")
    log(f"downloaded {len(manifest)} videos")
    return 0 if len(manifest) >= TARGET_COUNT else 1


if __name__ == "__main__":
    raise SystemExit(main())
