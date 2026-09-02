FROM python:3.12-slim

WORKDIR /app

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Tell HuggingFace where to cache models inside the image
ENV TRANSFORMERS_CACHE=/app/.cache/huggingface \
    HF_HOME=/app/.cache/huggingface

# Install system dependencies (single RUN layer to keep image smaller)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    net-tools \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set up user permissions
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser
RUN chown -R appuser:appuser /app

# ── PyTorch (CPU-only) ────────────────────────────────────────────────────────
# Install CPU-only PyTorch BEFORE requirements.txt so the much larger CUDA
# build (~1.7 GB) is never pulled.  torch is excluded from requirements.txt
# and installed here instead.
RUN pip install --no-cache-dir \
    torch>=2.10.0 \
    --index-url https://download.pytorch.org/whl/cpu

# ── Transformers and accelerate ───────────────────────────────────────────────
RUN pip install --no-cache-dir transformers>=4.40 accelerate>=0.2

# ── Copy remaining requirements and install ───────────────────────────────────
COPY requirements-img.txt .
RUN pip install --no-cache-dir -r requirements-img.txt

# Copy application code and install as an editable package so the `oli` and
# `oli-server` console scripts are available on PATH.
COPY . .
RUN pip install --no-cache-dir .

USER appuser

# Each compose service picks its own console script entrypoint.
ENTRYPOINT []
