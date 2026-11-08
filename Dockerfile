FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY run.py .

# Never run as root inside the container.
RUN useradd --system --uid 1000 --home-dir /app serverguard \
    && mkdir -p /app/data \
    && chown -R serverguard:serverguard /app/data
USER serverguard

# Inside the container the app must listen on all interfaces; compose decides
# which host address the port is published on.
ENV SERVERGUARD_HOST=0.0.0.0
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/api/health', timeout=3)"

CMD ["python", "run.py"]
