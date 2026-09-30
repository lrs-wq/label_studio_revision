FROM docker.m.daocloud.io/ultralytics/ultralytics:latest-cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=4 \
    YOLO_CONFIG_DIR=/tmp/Ultralytics \
    MPLCONFIGDIR=/tmp/matplotlib

WORKDIR /app

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY app.py .

EXPOSE 9090
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/health', timeout=5).read()"

CMD ["gunicorn", "--bind", "0.0.0.0:9090", "--workers", "1", "--threads", "2", "--timeout", "300", "app:app"]
