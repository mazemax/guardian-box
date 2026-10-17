# GuardianBox Dashboard
# Note: In production, dashboard is served by the orchestrator.
# This Dockerfile is for standalone development only.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir uvicorn

COPY static/ ./static/
COPY templates/ ./templates/

# Simple static file server
RUN echo 'from http.server import SimpleHTTPRequestHandler, HTTPServer\n\
import os\n\
os.chdir("/app")\n\
HTTPServer(("0.0.0.0", 3000), SimpleHTTPRequestHandler).serve_forever()' > serve.py

EXPOSE 3000

CMD ["python", "serve.py"]
