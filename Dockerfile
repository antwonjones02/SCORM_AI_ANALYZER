# SCORM AI Analyzer — web service
# Build:  docker build -t scorm-analyzer .
# Run:    docker run -p 8080:8080 -e ANTHROPIC_API_KEY=sk-ant-... scorm-analyzer

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    SCORMWEB_DATA_DIR=/data/web_jobs \
    SCORM_OUTPUT_DIR=/data/output

WORKDIR /app

RUN pip install --no-cache-dir \
        fastapi 'uvicorn[standard]' python-multipart \
        playwright anthropic requests openpyxl \
    && playwright install chromium --with-deps \
    && rm -rf /var/lib/apt/lists/*

COPY scormlib/ scormlib/
COPY scormweb/ scormweb/
COPY scormshop.py scorm_analyzer.py ./

RUN mkdir -p /data/web_jobs

EXPOSE 8080
CMD ["python3", "-m", "scormweb"]
