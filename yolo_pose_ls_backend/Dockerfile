FROM ultralytics/ultralytics:latest-cpu

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY app.py .

EXPOSE 9090
CMD ["gunicorn", "--bind", "0.0.0.0:9090", "--workers", "1", "--threads", "2", "--timeout", "240", "app:app"]
