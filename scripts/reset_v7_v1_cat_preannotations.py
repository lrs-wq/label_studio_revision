import os

import reset_v6_preannotations as base

from ml.models import MLBackend  # noqa: E402
from projects.models import Project  # noqa: E402


MODEL_VERSION = os.environ.get(
    "MODEL_VERSION",
    "cat_feeding_head_v7_yolo26s_tune2_200e_b32_wandb",
)
TARGET_PROJECT_ID = int(os.environ.get("TARGET_PROJECT_ID", "5"))
EXPECTED_PROJECT_TITLE = os.environ.get("EXPECTED_PROJECT_TITLE", "v1_cat")


def target_backend_rows():
    rows = list(
        MLBackend.objects.select_related("project")
        .filter(url=base.ML_URL, project_id=TARGET_PROJECT_ID)
        .order_by("id")
    )
    if not rows:
        raise RuntimeError(
            f"No Label Studio ML backends found for project={TARGET_PROJECT_ID} url={base.ML_URL}"
        )
    return rows


def main():
    base.MODEL_VERSION = MODEL_VERSION
    base.healthcheck()

    project = Project.objects.get(pk=TARGET_PROJECT_ID)
    if EXPECTED_PROJECT_TITLE and project.title != EXPECTED_PROJECT_TITLE:
        raise RuntimeError(
            f"Project {TARGET_PROJECT_ID} title is {project.title!r}, expected {EXPECTED_PROJECT_TITLE!r}"
        )

    backends = target_backend_rows()
    base.log(
        "Targets: "
        + ", ".join(
            f"project={backend.project_id} backend={backend.id} title={backend.title!r}"
            for backend in backends
        )
    )

    base.sync_model_versions(backends)
    base.clear_old_predictions([TARGET_PROJECT_ID])

    backend = MLBackend.objects.get(pk=backends[0].id)
    project = Project.objects.get(pk=TARGET_PROJECT_ID)
    base.regenerate_for_project(project, backend)
    base.log(f"Project {TARGET_PROJECT_ID} v7 preannotations regenerated")


if __name__ == "__main__":
    main()
