import csv
import hashlib
import re
from pathlib import Path

import cv2


VIDEO_DIR = Path(r"D:\gogelila\label_studio\cat_video_assets")
OUTPUT_DIR = VIDEO_DIR / "frames_100"
FRAME_STEP = 100
JPEG_QUALITY = 95


def safe_output_name(video_path: Path) -> str:
    match = re.match(r"(\d+)", video_path.stem)
    if match:
        return f"v{match.group(1)}"
    digest = hashlib.sha1(str(video_path).encode("utf-8")).hexdigest()[:8]
    return f"v{digest}"


def extract_video(video_path: Path, writer: csv.DictWriter) -> tuple[int, int]:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        print(f"failed to open: {video_path}", flush=True)
        return 0, 0

    video_name = safe_output_name(video_path)

    frame_index = 0
    saved = 0
    total_read = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break

        if frame_index % FRAME_STEP == 0:
            image_path = OUTPUT_DIR / f"{video_name}__frame_{frame_index:06d}.jpg"
            ok = cv2.imwrite(str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                raise RuntimeError(f"failed to write image: {image_path}")
            writer.writerow(
                {
                    "video": str(video_path),
                    "image": str(image_path),
                    "frame_index": frame_index,
                }
            )
            saved += 1

        frame_index += 1
        total_read += 1

    capture.release()
    print(f"{video_path.name}: read {total_read} frames, saved {saved}", flush=True)
    return total_read, saved


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    videos = sorted(VIDEO_DIR.glob("*.mp4"))
    manifest_path = OUTPUT_DIR / "frames_manifest.csv"

    total_frames = 0
    total_images = 0
    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=["video", "image", "frame_index"])
        writer.writeheader()
        for video_path in videos:
            frame_count, image_count = extract_video(video_path, writer)
            total_frames += frame_count
            total_images += image_count

    print(f"videos: {len(videos)}", flush=True)
    print(f"total frames read: {total_frames}", flush=True)
    print(f"images saved: {total_images}", flush=True)
    print(f"manifest: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
