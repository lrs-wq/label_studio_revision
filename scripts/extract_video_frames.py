import argparse
import csv
import re
import shutil
from pathlib import Path

import cv2


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
JPEG_QUALITY = 95


def safe_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._-")
    return value or "video"


def unique_output_dir(root: Path, stem: str, used: set[str]) -> Path:
    base = safe_name(stem)
    candidate = base
    index = 2
    while candidate.lower() in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate.lower())
    return root / candidate


def extract_video(video_path: Path, output_dir: Path, interval: int, writer: csv.DictWriter) -> tuple[int, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        print(f"failed to open: {video_path}", flush=True)
        return 0, 0

    frame_index = 0
    saved_count = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break

        if frame_index % interval == 0:
            image_path = output_dir / f"frame_{frame_index:06d}.jpg"
            saved = cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not saved:
                capture.release()
                raise RuntimeError(f"failed to write image: {image_path}")
            writer.writerow(
                {
                    "video": str(video_path),
                    "image": str(image_path),
                    "frame_index": frame_index,
                }
            )
            saved_count += 1

        frame_index += 1
        if frame_index % 500 == 0:
            print(f"{video_path.name}: read {frame_index}, saved {saved_count}", flush=True)

    capture.release()
    print(f"{video_path.name}: read {frame_index}, saved {saved_count}", flush=True)
    return frame_index, saved_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video_dir", type=Path)
    parser.add_argument("--interval", type=int, default=1)
    parser.add_argument("--output-name", default="frames_1")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.interval < 1:
        raise ValueError("--interval must be >= 1")

    video_dir = args.video_dir.resolve()
    if not video_dir.exists():
        raise FileNotFoundError(video_dir)

    output_root = video_dir / args.output_name
    if output_root.exists() and args.clean:
        resolved_output = output_root.resolve()
        if not str(resolved_output).lower().startswith(str(video_dir).lower()):
            raise RuntimeError(f"refusing to delete outside video dir: {resolved_output}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    videos = sorted(path for path in video_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS)
    manifest_path = output_root / "frames_manifest.csv"

    total_frames = 0
    total_images = 0
    used_dirs: set[str] = set()
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=["video", "image", "frame_index"])
        writer.writeheader()
        for video_path in videos:
            video_output_dir = unique_output_dir(output_root, video_path.stem, used_dirs)
            frame_count, image_count = extract_video(video_path, video_output_dir, args.interval, writer)
            total_frames += frame_count
            total_images += image_count

    print(f"videos: {len(videos)}", flush=True)
    print(f"total frames read: {total_frames}", flush=True)
    print(f"images saved: {total_images}", flush=True)
    print(f"output: {output_root}", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
