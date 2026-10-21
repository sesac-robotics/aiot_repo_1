FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev build-essential ca-certificates \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Build InsightFace extensions against the installed NumPy headers.
RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel && \
    python3 -m pip install --no-cache-dir "numpy==1.26.4" cython && \
    python3 -m pip install --no-cache-dir --no-build-isolation \
        "insightface==0.7.3" \
        "opencv-python-headless==4.11.0.86" \
        "onnxruntime-gpu==1.20.2"

WORKDIR /app
