#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/root/label_studio}
MODEL_FILE=${MODEL_FILE:-/data/label_studio/models/dog_pose_yolo26m_best.pt}
MODEL_VERSION=${MODEL_VERSION:-dog_pose_yolo26m_best}
IMAGE=${IMAGE:-label-studio-dog-pose-preannot:20260822}
CONTAINER=${CONTAINER:-label-studio-dog-pose-preannot}
HOST_PORT=${HOST_PORT:-9094}

test -s "$MODEL_FILE"
test -f "$PROJECT_ROOT/dog_pose_ls_backend/Dockerfile"

docker build -t "$IMAGE" "$PROJECT_ROOT/dog_pose_ls_backend"

if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker rm -f "$CONTAINER"
fi

docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  --init \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=512m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --cpus 8 \
  --memory 8g \
  --shm-size 1g \
  -p "127.0.0.1:${HOST_PORT}:9090" \
  -e MODEL_PATH="/models/$(basename "$MODEL_FILE")" \
  -e MODEL_VERSION="$MODEL_VERSION" \
  -e LABEL_STUDIO_DATA_DIR=/label_studio_data \
  -e LOCAL_FILES_DOCUMENT_ROOT=/local_files \
  -e CONFIDENCE_THRESHOLD=0.20 \
  -e KPT_CONFIDENCE_THRESHOLD=0.20 \
  -e DEVICE=cpu \
  -v "$(dirname "$MODEL_FILE"):/models:ro" \
  -v "$PROJECT_ROOT/label_studio_data:/label_studio_data:ro" \
  -v /data/label_studio/media:/label_studio_data/media:ro \
  -v /data/label_studio/local_files:/local_files:ro \
  "$IMAGE"

for _attempt in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${HOST_PORT}/health" >/dev/null; then
    curl --fail --silent --show-error --max-time 10 \
      "http://127.0.0.1:${HOST_PORT}/health"
    echo
    exit 0
  fi
  sleep 5
done

docker logs --tail 100 "$CONTAINER" >&2
exit 1
