from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "cat_image_assets"
CACHE_DIR = OUT_DIR / "cache"
IMAGES_DIR = OUT_DIR / "images"
MANIFEST_PATH = OUT_DIR / "manifest.csv"
REPORT_PATH = OUT_DIR / "download_report.json"

ANNOTATIONS_URL = "http://images.cocodataset.org/annotations/annotations_trainval2017.zip"
ANNOTATIONS_ZIP = CACHE_DIR / "annotations_trainval2017.zip"
ANNOTATIONS_DIR = CACHE_DIR / "annotations"

CONTEXT_CATEGORIES = {"bowl", "toilet"}
PREFERRED_CATEGORY_ORDER = ("bowl", "toilet")


@dataclass(frozen=True)
class Candidate:
    subset: str
    image_id: int
    file_name: str
    coco_url: str
    flickr_url: str
    width: int
    height: int
    license_id: int
    category_names: tuple[str, ...]
    cat_annotation_count: int
    score: int


def request_url(url: str):
    request = Request(url, headers={"User-Agent": "label-studio-cat-image-downloader/1.0"})
    return urlopen(request, timeout=60)


def download_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    downloaded = tmp_path.stat().st_size if tmp_path.exists() else 0

    headers = {"User-Agent": "label-studio-cat-image-downloader/1.0"}
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"

    request = Request(url, headers=headers)
    with urlopen(request, timeout=60) as response:
        mode = "ab" if downloaded and response.status == 206 else "wb"
        if mode == "wb" and tmp_path.exists():
            tmp_path.unlink()
        with tmp_path.open(mode) as file_obj:
            shutil.copyfileobj(response, file_obj)

    tmp_path.replace(path)


def ensure_annotations() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not ANNOTATIONS_ZIP.exists():
        print(f"Downloading COCO annotations: {ANNOTATIONS_ZIP}")
        download_file(ANNOTATIONS_URL, ANNOTATIONS_ZIP)
    else:
        print(f"Using cached annotations: {ANNOTATIONS_ZIP}")

    needed = [
        ANNOTATIONS_DIR / "instances_train2017.json",
        ANNOTATIONS_DIR / "instances_val2017.json",
    ]
    if all(path.exists() for path in needed):
        return

    print(f"Extracting annotations to: {ANNOTATIONS_DIR}")
    ANNOTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ANNOTATIONS_ZIP) as archive:
        for member in archive.namelist():
            if member.endswith(("instances_train2017.json", "instances_val2017.json")):
                archive.extract(member, CACHE_DIR)


def load_split(subset: str) -> tuple[list[Candidate], list[dict]]:
    annotation_path = ANNOTATIONS_DIR / f"instances_{subset}2017.json"
    print(f"Reading {annotation_path}")
    data = json.loads(annotation_path.read_text(encoding="utf-8"))

    categories_by_id = {item["id"]: item["name"] for item in data["categories"]}
    licenses = data.get("licenses", [])
    cat_category_id = next(item["id"] for item in data["categories"] if item["name"] == "cat")
    context_ids = {
        item["id"]
        for item in data["categories"]
        if item["name"] in CONTEXT_CATEGORIES
    }

    image_categories: dict[int, set[str]] = {}
    cat_counts: dict[int, int] = {}
    for annotation in data["annotations"]:
        image_id = annotation["image_id"]
        category_id = annotation["category_id"]
        category_name = categories_by_id[category_id]

        if category_id == cat_category_id:
            cat_counts[image_id] = cat_counts.get(image_id, 0) + 1
            image_categories.setdefault(image_id, set()).add("cat")
        elif category_id in context_ids:
            image_categories.setdefault(image_id, set()).add(category_name)

    candidates: list[Candidate] = []
    for image in data["images"]:
        image_id = image["id"]
        if image_id not in cat_counts:
            continue

        names = image_categories.get(image_id, set())
        context_count = len(names.intersection(CONTEXT_CATEGORIES))
        score = context_count * 100 + min(cat_counts[image_id], 5) * 5
        if "bowl" in names:
            score += 50
        if "toilet" in names:
            score += 20

        candidates.append(
            Candidate(
                subset=subset,
                image_id=image_id,
                file_name=image["file_name"],
                coco_url=image["coco_url"],
                flickr_url=image.get("flickr_url", ""),
                width=image["width"],
                height=image["height"],
                license_id=image.get("license", 0),
                category_names=tuple(sorted(names, key=lambda name: (name != "cat", name))),
                cat_annotation_count=cat_counts[image_id],
                score=score,
            )
        )

    return candidates, licenses


def collect_candidates(limit: int, seed: int) -> tuple[list[Candidate], dict[int, dict]]:
    train_candidates, train_licenses = load_split("train")
    val_candidates, val_licenses = load_split("val")
    licenses = {item["id"]: item for item in train_licenses + val_licenses}

    all_candidates = train_candidates + val_candidates
    rng = random.Random(seed)
    rng.shuffle(all_candidates)
    all_candidates.sort(
        key=lambda item: (
            item.score,
            "bowl" in item.category_names,
            "toilet" in item.category_names,
            item.cat_annotation_count,
        ),
        reverse=True,
    )

    print(f"Found {len(all_candidates)} COCO images with cat annotations")
    selected = all_candidates[: max(limit * 2, limit + 200)]
    print(f"Selected {len(selected)} candidates for download attempts")
    return selected, licenses


def existing_manifest() -> dict[int, dict]:
    if not MANIFEST_PATH.exists():
        return {}
    with MANIFEST_PATH.open("r", newline="", encoding="utf-8") as file_obj:
        rows = list(csv.DictReader(file_obj))
    existing: dict[int, dict] = {}
    for row in rows:
        try:
            image_id = int(row["image_id"])
        except (KeyError, ValueError):
            continue
        local_path = Path(row.get("local_path", ""))
        if local_path.exists() and local_path.stat().st_size > 0:
            existing[image_id] = row
    return existing


def local_image_path(candidate: Candidate) -> Path:
    suffix = Path(candidate.file_name).suffix or ".jpg"
    contexts = [name for name in PREFERRED_CATEGORY_ORDER if name in candidate.category_names]
    context_part = "_".join(contexts) if contexts else "cat"
    return IMAGES_DIR / f"{candidate.subset}_{candidate.image_id:012d}_{context_part}{suffix}"


def download_image(candidate: Candidate, retries: int = 3) -> tuple[Candidate, Path | None, str]:
    path = local_image_path(candidate)
    if path.exists() and path.stat().st_size > 0:
        return candidate, path, "cached"

    tmp_path = path.with_suffix(path.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            with request_url(candidate.coco_url) as response:
                if response.status >= 400:
                    return candidate, None, f"http {response.status}"
                IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                with tmp_path.open("wb") as file_obj:
                    shutil.copyfileobj(response, file_obj)
            if tmp_path.stat().st_size == 0:
                tmp_path.unlink(missing_ok=True)
                return candidate, None, "empty file"
            tmp_path.replace(path)
            return candidate, path, "downloaded"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            tmp_path.unlink(missing_ok=True)
            if attempt == retries:
                return candidate, None, f"{type(exc).__name__}: {exc}"
            time.sleep(0.5 * attempt)

    return candidate, None, "unknown error"


def write_manifest(rows: list[dict]) -> None:
    fieldnames = [
        "rank",
        "local_path",
        "subset",
        "image_id",
        "file_name",
        "width",
        "height",
        "cat_annotation_count",
        "context_labels",
        "all_selected_labels",
        "coco_url",
        "flickr_url",
        "license_name",
        "license_url",
    ]
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST_PATH.open("w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_manifest_row(rank: int, candidate: Candidate, path: Path, licenses: dict[int, dict]) -> dict:
    license_info = licenses.get(candidate.license_id, {})
    context_labels = [name for name in PREFERRED_CATEGORY_ORDER if name in candidate.category_names]
    return {
        "rank": rank,
        "local_path": str(path),
        "subset": candidate.subset,
        "image_id": candidate.image_id,
        "file_name": candidate.file_name,
        "width": candidate.width,
        "height": candidate.height,
        "cat_annotation_count": candidate.cat_annotation_count,
        "context_labels": "|".join(context_labels),
        "all_selected_labels": "|".join(candidate.category_names),
        "coco_url": candidate.coco_url,
        "flickr_url": candidate.flickr_url,
        "license_name": license_info.get("name", ""),
        "license_url": license_info.get("url", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download a local COCO image subset where every image has a cat annotation."
    )
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20260601)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    ensure_annotations()
    candidates, licenses = collect_candidates(args.target, args.seed)
    existing = existing_manifest()

    rows: list[dict] = []
    already_seen: set[int] = set()
    for candidate in candidates:
        row = existing.get(candidate.image_id)
        if not row:
            continue
        rows.append(row)
        already_seen.add(candidate.image_id)
        if len(rows) >= args.target:
            break

    to_download = [candidate for candidate in candidates if candidate.image_id not in already_seen]
    failures: list[dict] = []
    started = time.time()

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        candidate_iter = iter(to_download)
        pending = {}

        def submit_next() -> bool:
            try:
                candidate = next(candidate_iter)
            except StopIteration:
                return False
            pending[executor.submit(download_image, candidate)] = candidate
            return True

        for _ in range(max(1, args.workers) * 2):
            if not submit_next():
                break

        while pending and len(rows) < args.target:
            for future in as_completed(list(pending)):
                pending.pop(future, None)
            candidate, path, status = future.result()
            if path is not None:
                rank = len(rows) + 1
                rows.append(build_manifest_row(rank, candidate, path, licenses))
                if len(rows) % 50 == 0 or len(rows) == args.target:
                    print(f"Downloaded/verified {len(rows)}/{args.target}")
                if len(rows) >= args.target:
                    break
            else:
                failures.append(
                    {
                        "image_id": candidate.image_id,
                        "subset": candidate.subset,
                        "url": candidate.coco_url,
                        "error": status,
                    }
                )
            if len(rows) < args.target:
                submit_next()

    rows = rows[: args.target]
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    write_manifest(rows)

    context_counts: dict[str, int] = {}
    for row in rows:
        labels = row.get("context_labels", "")
        if not labels:
            context_counts["cat_only"] = context_counts.get("cat_only", 0) + 1
            continue
        for label in labels.split("|"):
            context_counts[label] = context_counts.get(label, 0) + 1

    report = {
        "target": args.target,
        "downloaded": len(rows),
        "image_dir": str(IMAGES_DIR),
        "manifest": str(MANIFEST_PATH),
        "source": "MS COCO 2017 train/val instances annotations",
        "source_url": ANNOTATIONS_URL,
        "context_counts": context_counts,
        "failures": failures[:100],
        "failure_count": len(failures),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))

    if len(rows) < args.target:
        print(f"Only downloaded {len(rows)} images; rerun to continue.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
