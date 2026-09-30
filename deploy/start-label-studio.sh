#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT=${PROJECT_ROOT:-/root/label_studio}
DATA_ROOT=${DATA_ROOT:-/data/label_studio}
DATA_DIR=${DATA_DIR:-$PROJECT_ROOT/label_studio_data}
MEDIA_DIR=${MEDIA_DIR:-$DATA_ROOT/media}
LOG_DIR=${LOG_DIR:-$PROJECT_ROOT/logs}
TMP_DIR=${TMP_DIR:-$PROJECT_ROOT/tmp}
PIP_CACHE_DIR=${PIP_CACHE_DIR:-$PROJECT_ROOT/pip_cache}
LOCAL_FILES_DOCUMENT_ROOT=${LOCAL_FILES_DOCUMENT_ROOT:-$DATA_ROOT/local_files}
LISTEN_HOST=${LISTEN_HOST:-127.0.0.1}
PUBLIC_HOST=${PUBLIC_HOST:-https://ai.hzzootech.com}
CSRF_TRUSTED_ORIGINS=${CSRF_TRUSTED_ORIGINS:-$PUBLIC_HOST}
PORT=${PORT:-8090}

mkdir -p "$DATA_DIR" "$MEDIA_DIR" "$LOG_DIR" "$TMP_DIR" "$PIP_CACHE_DIR" "$LOCAL_FILES_DOCUMENT_ROOT"

if [[ ! -L "$DATA_DIR/media" ]]; then
  if [[ -e "$DATA_DIR/media" ]]; then
    mv "$DATA_DIR/media" "$DATA_DIR/media.before_data_symlink_$(date +%Y%m%d%H%M%S)"
  fi
  ln -s "$MEDIA_DIR" "$DATA_DIR/media"
fi

if [[ ! -s "$DATA_DIR/secret_key.txt" ]]; then
  umask 077
  python3 - <<'PY' > "$DATA_DIR/secret_key.txt"
import secrets
print(secrets.token_urlsafe(64))
PY
fi

export HOME=/root
export BASE_DATA_DIR="$DATA_DIR"
export LABEL_STUDIO_BASE_DATA_DIR="$DATA_DIR"
export COLLECT_ANALYTICS=false
export LATEST_VERSION_CHECK=false
export DISABLE_SIGNUP_WITHOUT_LINK=true
export HOST="$PUBLIC_HOST"
export FRONTEND_HOSTNAME="$PUBLIC_HOST"
export CSRF_TRUSTED_ORIGINS
export LOCAL_FILES_SERVING_ENABLED=true
export LOCAL_FILES_DOCUMENT_ROOT="$LOCAL_FILES_DOCUMENT_ROOT"
export DATA_UPLOAD_MAX_NUMBER_FILES=10000
export DATA_UPLOAD_MAX_MEMORY_SIZE=20000000000
export TMPDIR="$TMP_DIR"
export PIP_CACHE_DIR="$PIP_CACHE_DIR"
export SECRET_KEY
SECRET_KEY="$(tr -d '\r\n' < "$DATA_DIR/secret_key.txt")"

if [[ -f "$PROJECT_ROOT/scripts/apply_label_studio_signup_disabled_notice.py" ]]; then
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/apply_label_studio_signup_disabled_notice.py"
fi

if [[ -f "$PROJECT_ROOT/scripts/apply_label_studio_runtime_patches.py" ]]; then
  "$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/scripts/apply_label_studio_runtime_patches.py"
fi

exec "$PROJECT_ROOT/.venv/bin/label-studio" start \
  --data-dir "$DATA_DIR" \
  --internal-host "$LISTEN_HOST" \
  --host "$PUBLIC_HOST" \
  --port "$PORT" \
  --no-browser
