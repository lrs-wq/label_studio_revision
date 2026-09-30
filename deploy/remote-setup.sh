#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/root/label_studio}
DATA_ROOT=${DATA_ROOT:-/data/label_studio}
DEPLOY_DIR="$PROJECT_ROOT/deploy"

mkdir -p "$PROJECT_ROOT" "$DATA_ROOT" "$PROJECT_ROOT/label_studio_data" \
  "$PROJECT_ROOT/logs" "$PROJECT_ROOT/tmp" "$PROJECT_ROOT/pip_cache" \
  "$DATA_ROOT/media" "$DATA_ROOT/models" "$DATA_ROOT/local_files"

if [[ -e "$PROJECT_ROOT/label_studio_data/media" && ! -L "$PROJECT_ROOT/label_studio_data/media" ]]; then
  mv "$PROJECT_ROOT/label_studio_data/media" "$PROJECT_ROOT/label_studio_data/media.before_data_symlink_$(date +%Y%m%d%H%M%S)"
fi
ln -sfn "$DATA_ROOT/media" "$PROJECT_ROOT/label_studio_data/media"

if [[ -e "$DATA_ROOT/cat_video_assets" ]]; then
  if [[ -e "$PROJECT_ROOT/cat_video_assets" && ! -L "$PROJECT_ROOT/cat_video_assets" ]]; then
    mv "$PROJECT_ROOT/cat_video_assets" "$PROJECT_ROOT/cat_video_assets.before_data_symlink_$(date +%Y%m%d%H%M%S)"
  fi
  ln -sfn "$DATA_ROOT/cat_video_assets" "$PROJECT_ROOT/cat_video_assets"
fi

chmod +x "$DEPLOY_DIR/start-label-studio.sh"

if [[ ! -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  python3 -m venv "$PROJECT_ROOT/.venv"
fi

"$PROJECT_ROOT/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$PROJECT_ROOT/.venv/bin/python" -m pip install --prefer-binary "label-studio==1.23.0"

install -m 0644 "$DEPLOY_DIR/label-studio.service" /etc/systemd/system/label-studio.service
if command -v docker >/dev/null 2>&1; then
  install -m 0644 "$DEPLOY_DIR/label-studio-yolo.service" /etc/systemd/system/label-studio-yolo.service
  install -m 0644 "$DEPLOY_DIR/label-studio-yolo-pose.service" /etc/systemd/system/label-studio-yolo-pose.service
fi

systemctl daemon-reload
systemctl enable label-studio.service
