FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MAX_CONCURRENT_JOBS=10 \
    MAX_QUEUED_JOBS=50 \
    QUEUE_TIMEOUT_SECONDS=30 \
    MAX_UPLOAD_MB=50 \
    MAX_UNCOMPRESSED_MB=200 \
    MAX_PDF_COUNT=100 \
    MAX_COMPRESSION_RATIO=200 \
    WEB_CONCURRENCY=2

WORKDIR /app

RUN groupadd --system invoice && useradd --system --gid invoice --create-home invoice

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY archive_reader.py excel_writer.py invoice_reader.py processing.py validator.py server.py ./

USER invoice

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips='*'"]
