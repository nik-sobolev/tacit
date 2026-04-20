FROM python:3.11-slim

# System dependencies for ffmpeg (audio), playwright (JS-heavy pages)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# Install Python dependencies first (layer cache)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browser (for JS-heavy webpage ingestion)
RUN playwright install chromium --with-deps

# Copy source
COPY backend/ .
COPY frontend/ ../frontend/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
