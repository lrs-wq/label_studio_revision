#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/root/label_studio}
YOLO_VENV=${YOLO_VENV:-$PROJECT_ROOT/.venv-yolo}
DEPLOY_DIR="$PROJECT_ROOT/deploy"

if [[ ! -x "$YOLO_VENV/bin/python" ]]; then
  python3 -m venv "$YOLO_VENV"
fi

"$YOLO_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$YOLO_VENV/bin/python" -m pip install --prefer-binary \
  flask==3.0.3 \
  gunicorn==22.0.0 \
  requests \
  pillow \
  ultralytics

install -m 0644 "$DEPLOY_DIR/label-studio-yolo.service" /etc/systemd/system/label-studio-yolo.service
install -m 0644 "$DEPLOY_DIR/label-studio-yolo-pose.service" /etc/systemd/system/label-studio-yolo-pose.service

systemctl daemon-reload
systemctl enable label-studio-yolo.service label-studio-yolo-pose.service
