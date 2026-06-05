# Lean backend image for Railway (core RAG chat). Voice/OCR extras are off by default;
# see requirements-optional.txt to enable them on a larger instance.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libgomp1 is needed by torch's OpenMP backend at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install the CPU-only torch build first so pip doesn't pull the huge CUDA wheel.
RUN pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

# Bake the MiniLM embedding model into the image so the first request is fast and the
# container doesn't depend on a model download at boot.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY fast_api.py .

# Railway provides $PORT at runtime; default to 8000 for local `docker run`.
EXPOSE 8000
CMD uvicorn fast_api:app --host 0.0.0.0 --port ${PORT:-8000}
