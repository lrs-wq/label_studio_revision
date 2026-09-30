from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path, PurePosixPath

from rest_framework.exceptions import ValidationError


logger = logging.getLogger(__name__)

TRANSCODE_SOURCE_EXTENSIONS = frozenset({".mov", ".mkv"})
DEFAULT_TIMEOUT_SECONDS = 6 * 60 * 60
DEFAULT_THREADS = 2
DEFAULT_PRESET = "veryfast"
DEFAULT_CRF = "23"


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw_value!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be greater than zero, got {value}")
    return value


def _executable(env_name: str, default_name: str) -> str:
    configured = os.getenv(env_name)
    executable = configured or shutil.which(default_name)
    if not executable:
        raise RuntimeError(
            f"{default_name} was not found; set {env_name} to its absolute path"
        )
    return executable


def _error_tail(value: str, limit: int = 2000) -> str:
    cleaned = " ".join((value or "").strip().split())
    return cleaned[-limit:] if cleaned else "no diagnostic output"


def _probe_mp4(path: Path, timeout_seconds: int) -> dict:
    ffprobe = _executable("LABEL_STUDIO_VIDEO_FFPROBE", "ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=min(timeout_seconds, 300),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe timed out while validating the MP4 output") from exc

    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe validation failed: {_error_tail(completed.stderr)}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid JSON") from exc

    video_streams = [
        stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise RuntimeError("the converted MP4 does not contain a video stream")
    if video_streams[0].get("codec_name") != "h264":
        raise RuntimeError(
            f"the converted video codec is {video_streams[0].get('codec_name')!r}, expected 'h264'"
        )

    format_names = set((payload.get("format", {}).get("format_name") or "").split(","))
    if "mp4" not in format_names:
        raise RuntimeError("ffprobe did not recognize the converted output as MP4")

    return payload


def _transcode(source_path: Path, temporary_path: Path, timeout_seconds: int) -> None:
    ffmpeg = _executable("LABEL_STUDIO_VIDEO_FFMPEG", "ffmpeg")
    threads = _positive_int_env("LABEL_STUDIO_VIDEO_TRANSCODE_THREADS", DEFAULT_THREADS)
    preset = os.getenv("LABEL_STUDIO_VIDEO_TRANSCODE_PRESET", DEFAULT_PRESET)
    crf = os.getenv("LABEL_STUDIO_VIDEO_TRANSCODE_CRF", DEFAULT_CRF)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-threads",
        str(threads),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
        "-tag:v",
        "avc1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-max_muxing_queue_size",
        "4096",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(temporary_path),
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg timed out after {timeout_seconds} seconds"
        ) from exc

    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {_error_tail(completed.stderr)}")
    if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
        raise RuntimeError("ffmpeg did not create a non-empty MP4 output")

    _probe_mp4(temporary_path, timeout_seconds)


def transcode_uploaded_video(file_upload) -> bool:
    """Convert an uploaded MOV/MKV to MP4 and update its FileUpload row atomically.

    The source is removed only after ffmpeg and ffprobe succeed and the database row
    points at the final MP4. Other upload formats are returned unchanged.
    """

    original_name = str(file_upload.file.name)
    source_suffix = PurePosixPath(original_name).suffix.lower()
    if source_suffix not in TRANSCODE_SOURCE_EXTENSIONS:
        return False

    timeout_seconds = _positive_int_env(
        "LABEL_STUDIO_VIDEO_TRANSCODE_TIMEOUT", DEFAULT_TIMEOUT_SECONDS
    )
    source_path = Path(file_upload.file.path)
    target_name = str(PurePosixPath(original_name).with_suffix(".mp4"))
    target_path = source_path.with_suffix(".mp4")
    temporary_path = target_path.with_name(
        f".{target_path.stem}.transcoding-{uuid.uuid4().hex}.mp4"
    )

    if not source_path.is_file():
        raise ValidationError(f"Uploaded source file was not found: {source_path.name}")
    if target_path.exists():
        raise ValidationError(f"Refusing to overwrite existing MP4: {target_path.name}")

    source_size = source_path.stat().st_size
    started_at = time.monotonic()
    database_updated = False

    try:
        _transcode(source_path, temporary_path, timeout_seconds)
        os.replace(temporary_path, target_path)

        file_upload.file.name = target_name
        file_upload.save(update_fields=["file"])
        database_updated = True

        source_path.unlink()
    except Exception as exc:
        file_upload.file.name = original_name
        if database_updated:
            try:
                file_upload.save(update_fields=["file"])
            except Exception:
                logger.exception("Failed to restore FileUpload %s after transcode error", file_upload.pk)
        target_path.unlink(missing_ok=True)
        raise ValidationError(
            f"Failed to convert {PurePosixPath(original_name).name} to MP4: {exc}"
        ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    file_upload.__dict__.pop("filepath", None)
    file_upload.__dict__.pop("file_name", None)
    target_size = target_path.stat().st_size
    logger.info(
        "Converted uploaded video %s to %s in %.1fs (%d -> %d bytes)",
        original_name,
        target_name,
        time.monotonic() - started_at,
        source_size,
        target_size,
    )
    return True


def cleanup_failed_upload(file_upload) -> None:
    """Best-effort cleanup for a FileUpload whose post-save conversion failed."""

    try:
        if file_upload.file and file_upload.file.name:
            file_upload.file.delete(save=False)
    except Exception:
        logger.exception("Failed to remove source file for FileUpload %s", file_upload.pk)

    try:
        if file_upload.pk:
            file_upload.delete()
    except Exception:
        logger.exception("Failed to remove FileUpload row %s", file_upload.pk)
