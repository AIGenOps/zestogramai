FROM python:3.12-slim

# Install ffmpeg and nodejs for yt-dlp processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade -r requirements.txt

# Create a non-root user and give full permissions on /data directory
RUN useradd -m botuser && \
    mkdir -p /data/tmp /data/downloads /data/logs && \
    chown -R botuser:botuser /data && \
    chmod -R 777 /data

COPY src/ ./src/

USER botuser

# Environment variables
ENV PYTHONPATH=/app
EXPOSE 10000

CMD ["python", "-m", "src.bot"]
