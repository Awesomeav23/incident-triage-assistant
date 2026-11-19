# API image: FastAPI + local embedding model baked in (no download at start-up).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTEMBED_CACHE_PATH=/opt/fastembed

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download bge-small once at build time.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"

COPY app app
COPY data data
COPY scripts scripts
COPY lambda_fn lambda_fn

RUN useradd --create-home --uid 10001 triage && chown -R triage /opt/fastembed
USER triage

EXPOSE 8030
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]
