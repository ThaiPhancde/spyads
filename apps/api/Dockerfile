FROM python:3.11-slim
# ffmpeg/ffprobe: video duration, resolution and poster frames for the Creative Vault
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Google Chrome for the headless TikTok Creative Center collector (browser_worker.launch drives the installed browser)
RUN playwright install --with-deps chrome && rm -rf /var/lib/apt/lists/*
COPY app ./app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data BROWSER_CHANNEL=chrome
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
