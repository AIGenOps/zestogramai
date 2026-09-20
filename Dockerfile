FROM python:3.12-slim

# Install ffmpeg for yt-dlp processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create a non-root user and give ownership of the data directory
RUN useradd -m botuser && \
    mkdir -p /data && \
    chown botuser:botuser /data

COPY src/ ./src/

USER botuser

# Environment variables
ENV PYTHONPATH=/app/src

CMD ["python", "-m", "src.bot"]
