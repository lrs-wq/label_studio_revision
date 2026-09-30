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
PROJECT_ID = 8

EXPERT_INSTRUCTION = """
YOLO Pose 可见性约定：
0 = 未标注/不可见：不要放这个关键点，导出时自动写 0。
1 = 遮挡但可推断：放点后选“1 遮挡但可推断”。
2 = 可见：放点后选“2 可见”。
左右以猫自身左右为准，不以画面左右为准。
""".strip()

KEYPOINT_LABELS = [
    ("鼻尖", "#E53935", "1"),
    ("左眼", "#8E24AA", "2"),
    ("右眼", "#5E35B1", "3"),
    ("颈背", "#3949AB", "4"),
    ("尾根", "#1E88E5", "5"),
    ("左前肩", "#039BE5", "6"),
    ("左前肘", "#00ACC1", "7"),
    ("左前爪", "#00897B", "8"),
    ("右前肩", "#43A047", "9"),
    ("右前肘", "#7CB342", "q"),
    ("右前爪", "#C0CA33", "w"),
    ("左后髋", "#FDD835", "e"),
    ("左后膝", "#FFB300", "r"),
    ("左后爪", "#FB8C00", "t"),
    ("右后髋", "#F4511E", "a"),
    ("右后膝", "#6D4C41", "s"),
    ("右后爪", "#546E7A", "d"),
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
    backup_path = DATA_DIR / f"label_studio.before_fix_cat_pose_keypoint_only_{timestamp}.sqlite3"
    with sqlite3.connect(DB_PATH) as src, sqlite3.connect(backup_path) as dst:
        src.backup(dst)
    return backup_path


def build_label_config():
    labels = []
    for value, color, hotkey in KEYPOINT_LABELS:
        labels.append(
            f'    <Label value="{escape(value)}" background="{color}" hotkey="{escape(hotkey)}"/>'
        )
    return "\n".join(
        [
            "<View>",
            '  <Image name="image" value="$image" zoom="true" zoomControl="true" rotateControl="true"/>',
            '  <KeyPointLabels name="pose_keypoints" toName="image" strokeWidth="2" pointSize="medium" opacity="0.95">',
            *labels,
            "  </KeyPointLabels>",
            '  <View visibleWhen="region-selected">',
            '    <Choices name="visibility" toName="image" perRegion="true" choice="single" required="true" showInline="true">',
            '      <Choice value="1" html="1 遮挡但可推断" hotkey="z"/>',
            '      <Choice value="2" html="2 可见" hotkey="x" selected="true"/>',
            "    </Choices>",
            "  </View>",
            "</View>",
        ]
    )


def main():
    configure_label_studio_environment()

    import django

    django.setup()

    from django.db import transaction
    from projects.models import Project
    from tasks.functions import update_tasks_counters
    from tasks.models import Prediction, Task

    project = Project.objects.get(id=PROJECT_ID)
    label_config = build_label_config()
    project.validate_label_config(label_config)
    backup_path = backup_database()

    with transaction.atomic():
        project.label_config = label_config
        project.expert_instruction = EXPERT_INSTRUCTION
        project.show_instruction = True
        project.parsed_label_config = None
        project.show_collab_predictions = False
        project.save(update_fields=["label_config", "expert_instruction", "show_instruction", "parsed_label_config", "show_collab_predictions"])

        predictions_deleted = Prediction.objects.filter(project=project).delete()[0]
        task_qs = Task.objects.filter(project=project)
        task_qs.update(total_predictions=0)

    counters_updated = update_tasks_counters(Task.objects.filter(project=project), from_scratch=True)

    print(f"backup={backup_path}")
    print(f"project_id={project.id}")
    print(f"predictions_deleted={predictions_deleted}")
    print(f"counters_updated={counters_updated}")
    print(f"keypoint_labels={len(KEYPOINT_LABELS)}")


if __name__ == "__main__":
    main()
