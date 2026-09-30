import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_ROOT = Path(r"D:\Anaconda3\envs\label-studio")
DATA_DIR = PROJECT_ROOT / "label_studio_data"
DB_PATH = DATA_DIR / "label_studio.sqlite3"
SECRET_KEY_FILE = DATA_DIR / "secret_key.txt"
SOURCE_DIR = Path(r"D:\gogelila\Eating_behavior\data\v9_yolo26_fur_color_cls")
PROJECT_ID = 7
EXTRA_DISPLAY_NAMES = {
    "mixed_white_ragdoll": "\u6df7\u767d/\u5e03\u5076",
    "hairless_cat": "\u65e0\u6bdb\u732b",
    "blue_cat": "\u84dd\u732b",
    "silver_gray_white": "\u94f6\u767d/\u7070\u767d",
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


def configure_label_studio_environment():
    site_label_studio = ENV_ROOT / "Lib" / "site-packages" / "label_studio"
    sys.path.insert(0, str(site_label_studio))

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.label_studio")
    os.environ["BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LABEL_STUDIO_BASE_DATA_DIR"] = str(DATA_DIR)
    os.environ["LATEST_VERSION_CHECK"] = "false"
    if SECRET_KEY_FILE.exists():
        os.environ["SECRET_KEY"] = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()


def backup_database():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = DATA_DIR / f"label_studio.before_fur_color_zh_hotkeys_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path


def load_labels():
    labels = [
        line.strip()
        for line in (SOURCE_DIR / "classes.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for label in EXTRA_DISPLAY_NAMES:
        if label not in labels:
            labels.append(label)
    return order_labels(labels)


def order_labels(labels):
    ordered = [label for label in LABEL_ORDER if label in labels]
    remaining = [label for label in labels if label not in LABEL_ORDER]
    if "mixed_other" in ordered:
        ordered = [label for label in ordered if label != "mixed_other"]
        return ordered + remaining + ["mixed_other"]
    return ordered + remaining


def load_display_names(labels):
    display_names = {label: label for label in labels}
    zh_path = SOURCE_DIR / "classes_zh.txt"
    if zh_path.exists():
        for line in zh_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2 and parts[0] in display_names:
                display_names[parts[0]] = parts[1].strip()
    display_names.update(EXTRA_DISPLAY_NAMES)
    return display_names


def build_label_config(labels, display_names):
    choices = "\n".join(build_choice_xml(index, label, display_names[label]) for index, label in enumerate(labels, start=1))
    return f"""
<View>
  <Image name="image" value="$image"/>
  <Choices name="fur_color" toName="image" choice="single" showInLine="true" required="true">
{choices}
  </Choices>
</View>
""".strip()


def hotkey_for_label(index, label):
    if label == "mixed_other":
        return "0"
    if 1 <= index <= 9:
        return str(index)
    return ""


def build_choice_xml(index, label, display_name):
    hotkey = hotkey_for_label(index, label)
    hotkey_attr = f' hotkey="{hotkey}"' if hotkey else ""
    return (
        f'    <Choice value="{escape(label)}" '
        f'html="{index} {escape(display_name)}" '
        f'hint="{escape(label)}"{hotkey_attr}/>'
    )


def main():
    configure_label_studio_environment()

    import django

    django.setup()

    from projects.models import Project

    labels = load_labels()
    display_names = load_display_names(labels)
    label_config = build_label_config(labels, display_names)
    backup_path = backup_database()

    project = Project.objects.get(id=PROJECT_ID)
    project.validate_label_config(label_config)
    project.label_config = label_config
    project.save(update_fields=["label_config"])

    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"title={project.title}")
    print(
        "shortcuts="
        + ",".join(
            f"{index}[{hotkey_for_label(index, label)}]:{display_names[label]}"
            for index, label in enumerate(labels, start=1)
        )
    )


if __name__ == "__main__":
    main()
