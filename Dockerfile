FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    INSTANCE_DIR=/data TRUST_PROXY=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py run.py ./

RUN useradd --system --uid 10001 appuser && mkdir -p /data && chown appuser /data
USER appuser
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# 維持單一 worker 多執行緒：登入限流的計數存在程序記憶體內，多個 worker 會各算各的
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
