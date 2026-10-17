# GuardianBox Vision Service
# CPU-capable with optional GPU, works on ARM64 (DGX Spark) and x86_64
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Try to download YOLO pose weights at build time (with network: host)
# This may fail if network is restricted, which is fine - we have fallbacks
RUN python -c "from ultralytics import YOLO; YOLO('yolo11n-pose.pt')" 2>/dev/null || \
    echo "YOLO weights not downloaded at build time - will use fallback pose detection"

COPY vision_service.py .

# Create samples directory mount point
RUN mkdir -p /samples

EXPOSE 8004

# Health check
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8004/health || exit 1

CMD ["uvicorn", "vision_service:app", "--host", "0.0.0.0", "--port", "8004"]
