from __future__ import annotations

import shutil
from pathlib import Path


VERSION = "20260826-2"
FRONTEND_UPLOAD_PATCH_VERSION = "mov-mkv-20260821-1"
CUSTOM_JS_NAME = "custom-pose-visibility.js"
VIDEO_TRANSCODER_NAME = "video_upload_transcode.py"
VIDEO_UPLOAD_PATCH_MARKER = "label-studio-video-upload-transcode-v1"
VIDEO_UPLOAD_IMPORT = (
    "from .video_upload_transcode import cleanup_failed_upload, transcode_uploaded_video"
)
CUSTOM_JS_TAG = (
    '<script src="{{settings.FRONTEND_HOSTNAME}}/react-app/'
    f'{CUSTOM_JS_NAME}?v={VERSION}"></script>'
)
MAIN_JS_TAG = (
    '<script src="{{settings.FRONTEND_HOSTNAME}}/react-app/main.js?'
    f'v={{{{ versions.backend.commit }}}}-{FRONTEND_UPLOAD_PATCH_VERSION}"></script>'
)
FRONTEND_VIDEO_FORMATS_OLD = 'video:["mp4","webm"]'
FRONTEND_VIDEO_FORMATS_NEW = 'video:["mp4","webm","mov","mkv"]'
APP_JS_END = "{% endblock %}"
SHORTCUT_MARKERS = (
    "__labelStudioCatTaskShortcuts",
    'handleTaskShortcut("next", true)',
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def label_studio_package_dir() -> Path:
    import label_studio

    return Path(label_studio.__file__).resolve().parent


def patch_source_dir() -> Path:
    return repo_root() / "patches" / "label_studio"


def read_patch_asset(name: str) -> str:
    return (patch_source_dir() / name).read_text(encoding="utf-8-sig")


def copy_if_changed(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and source.read_bytes() == target.read_bytes():
        return f"already current: {target}"

    shutil.copy2(source, target)
    return f"copied: {target}"


def backup_once(path: Path) -> None:
    backup = path.with_name(f"{path.name}.before_label_studio_runtime_patches.bak")
    if not backup.exists():
        shutil.copy2(path, backup)


def install_custom_pose_visibility_js(site_packages_dir: Path) -> str:
    source = patch_source_dir() / CUSTOM_JS_NAME
    if not source.exists():
        raise FileNotFoundError(source)

    target = site_packages_dir / "web" / "dist" / "apps" / "labelstudio" / CUSTOM_JS_NAME
    return copy_if_changed(source, target)


def install_video_upload_transcoder(package_dir: Path) -> str:
    source = patch_source_dir() / VIDEO_TRANSCODER_NAME
    if not source.exists():
        raise FileNotFoundError(source)

    target = package_dir / "data_import" / VIDEO_TRANSCODER_NAME
    return copy_if_changed(source, target)


def patch_supported_video_extensions(package_dir: Path) -> str:
    settings_file = package_dir / "core" / "settings" / "base.py"
    text = settings_file.read_text(encoding="utf-8")
    marker = f"# {VIDEO_UPLOAD_PATCH_MARKER}"
    if marker in text:
        return f"already supports MOV/MKV uploads: {settings_file}"

    anchor = "        '.mp4',"
    if anchor not in text:
        raise RuntimeError(f"Expected MP4 extension anchor was not found: {settings_file}")

    addition = (
        f"        '.mov',  {marker}\n"
        f"        '.mkv',  {marker}"
    )
    text = text.replace(anchor, f"{anchor}\n{addition}", 1)
    backup_once(settings_file)
    settings_file.write_text(text, encoding="utf-8")
    return f"enabled MOV/MKV uploads: {settings_file}"


def patch_video_upload_hook(package_dir: Path) -> str:
    uploader = package_dir / "data_import" / "uploader.py"
    text = uploader.read_text(encoding="utf-8")
    changed = False

    if VIDEO_UPLOAD_IMPORT not in text:
        import_anchor = "from .models import FileUpload"
        if import_anchor not in text:
            raise RuntimeError(f"Expected FileUpload import was not found: {uploader}")
        text = text.replace(import_anchor, f"{import_anchor}\n{VIDEO_UPLOAD_IMPORT}", 1)
        changed = True

    function_start = text.find("def create_file_upload(")
    function_end = text.find("\ndef allowlist_svg(", function_start)
    if function_start == -1 or function_end == -1:
        raise RuntimeError(f"Expected create_file_upload function was not found: {uploader}")

    function_text = text[function_start:function_end]
    if "transcode_uploaded_video(instance)" not in function_text:
        save_anchor = "    instance.save()\n    return instance"
        if save_anchor not in function_text:
            raise RuntimeError(f"Expected FileUpload save anchor was not found: {uploader}")
        replacement = (
            "    instance.save()\n"
            "    try:\n"
            "        transcode_uploaded_video(instance)\n"
            "    except Exception:\n"
            "        cleanup_failed_upload(instance)\n"
            "        raise\n"
            "    return instance"
        )
        function_text = function_text.replace(save_anchor, replacement, 1)
        text = text[:function_start] + function_text + text[function_end:]
        changed = True

    if not changed:
        return f"already transcodes MOV/MKV uploads: {uploader}"

    backup_once(uploader)
    uploader.write_text(text, encoding="utf-8")
    return f"installed MOV/MKV upload hook: {uploader}"


def patch_frontend_video_formats(site_packages_dir: Path) -> str:
    main_js = site_packages_dir / "web" / "dist" / "apps" / "labelstudio" / "main.js"
    text = main_js.read_text(encoding="utf-8")

    if FRONTEND_VIDEO_FORMATS_NEW in text:
        return f"already shows MOV/MKV in upload UI: {main_js}"

    occurrences = text.count(FRONTEND_VIDEO_FORMATS_OLD)
    if occurrences != 1:
        raise RuntimeError(
            f"Expected one frontend video-format anchor in {main_js}, found {occurrences}"
        )

    text = text.replace(FRONTEND_VIDEO_FORMATS_OLD, FRONTEND_VIDEO_FORMATS_NEW, 1)
    backup_once(main_js)
    main_js.write_text(text, encoding="utf-8")
    return f"enabled MOV/MKV in upload UI: {main_js}"


def shortcut_snippet() -> str:
    snippet = read_patch_asset("task-shortcuts.html").strip()
    if "__labelStudioCatTaskShortcuts" not in snippet:
        snippet = snippet.replace(
            "var shortcutInProgress = false;",
            f'window.__labelStudioCatTaskShortcuts = "{VERSION}";\n'
            "    var shortcutInProgress = false;",
            1,
        )
    return snippet


def insert_once(text: str, anchor: str, addition: str) -> tuple[str, bool]:
    if addition in text:
        return text, False
    if anchor not in text:
        raise RuntimeError(f"Expected anchor was not found: {anchor}")
    return text.replace(anchor, f"{anchor}\n{addition}", 1), True


def ensure_custom_js_tag(text: str) -> tuple[str, bool]:
    if CUSTOM_JS_NAME in text:
        lines = text.splitlines(keepends=True)
        changed = False
        for index, line in enumerate(lines):
            if CUSTOM_JS_NAME not in line:
                continue
            newline = "\n" if line.endswith("\n") else ""
            replacement = CUSTOM_JS_TAG + newline
            if line != replacement:
                lines[index] = replacement
                changed = True
        return "".join(lines), changed

    return insert_once(text, MAIN_JS_TAG, CUSTOM_JS_TAG)


def ensure_main_js_cache_version(text: str) -> tuple[str, bool]:
    lines = text.splitlines(keepends=True)
    matches = 0
    changed = False

    for index, line in enumerate(lines):
        if "/react-app/main.js?" not in line:
            continue
        matches += 1
        newline = "\n" if line.endswith("\n") else ""
        indentation = line[: len(line) - len(line.lstrip())]
        replacement = indentation + MAIN_JS_TAG + newline
        if line != replacement:
            lines[index] = replacement
            changed = True

    if matches != 1:
        raise RuntimeError(f"Expected one Label Studio main.js tag, found {matches}")

    return "".join(lines), changed


def insert_after_app_js_block(text: str, addition: str) -> tuple[str, bool]:
    if addition in text:
        return text, False

    start = text.find("{% block app-js %}")
    if start == -1:
        raise RuntimeError("Expected app-js block was not found")

    end = text.find(APP_JS_END, start)
    if end == -1:
        raise RuntimeError("Expected app-js endblock was not found")

    insert_at = end + len(APP_JS_END)
    return text[:insert_at] + f"\n\n{addition}" + text[insert_at:], True


def ensure_shortcut_snippet(text: str) -> tuple[str, bool]:
    snippet = shortcut_snippet()
    marker_positions = [text.find(marker) for marker in SHORTCUT_MARKERS]
    marker_positions = [position for position in marker_positions if position != -1]

    if not marker_positions:
        return insert_after_app_js_block(text, snippet)

    marker_position = min(marker_positions)
    script_start = text.rfind("<script", 0, marker_position)
    script_end = text.find("</script>", marker_position)
    if script_start == -1 or script_end == -1:
        raise RuntimeError("Existing task shortcut script boundaries were not found")

    script_end += len("</script>")
    existing = text[script_start:script_end]
    if existing == snippet:
        return text, False

    return text[:script_start] + snippet + text[script_end:], True


def patch_base_html(package_dir: Path) -> list[str]:
    base_html = package_dir / "templates" / "base.html"
    text = base_html.read_text(encoding="utf-8")
    results: list[str] = []
    changed = False

    text, main_js_changed = ensure_main_js_cache_version(text)
    changed = changed or main_js_changed
    if main_js_changed:
        results.append(f"updated main.js cache version: {base_html}")
    else:
        results.append(f"already has current main.js cache version: {base_html}")

    had_custom_js = CUSTOM_JS_NAME in text
    if had_custom_js and CUSTOM_JS_TAG in text:
        results.append(f"already has custom JS tag: {base_html}")
    else:
        text, inserted = ensure_custom_js_tag(text)
        changed = changed or inserted
        action = "updated" if had_custom_js else "inserted"
        results.append(f"{action} custom JS tag: {base_html}")

    had_shortcuts = any(marker in text for marker in SHORTCUT_MARKERS)
    text, shortcuts_changed = ensure_shortcut_snippet(text)
    changed = changed or shortcuts_changed
    if shortcuts_changed:
        action = "updated" if had_shortcuts else "inserted"
        results.append(f"{action} task shortcuts: {base_html}")
    else:
        results.append(f"already has current task shortcuts: {base_html}")

    if changed:
        backup_once(base_html)
        base_html.write_text(text, encoding="utf-8")

    return results


def main() -> None:
    package_dir = label_studio_package_dir()
    site_packages_dir = package_dir.parent

    results = [
        install_custom_pose_visibility_js(site_packages_dir),
        install_video_upload_transcoder(package_dir),
        patch_supported_video_extensions(package_dir),
        patch_video_upload_hook(package_dir),
        patch_frontend_video_formats(site_packages_dir),
        *patch_base_html(package_dir),
    ]
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
