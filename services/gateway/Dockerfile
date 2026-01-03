# API Gateway Dockerfile
# Lightweight FastAPI server for WebSocket handling

FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy gateway code
COPY gateway.py .

# Environment variables with defaults
ENV REDIS_HOST=redis \
    REDIS_PORT=6379 \
    QUEUE_NAME=llm_queue \
    CHANNEL_PREFIX=task_channel

# Expose port
EXPOSE 8000

# Run the gateway with uvicorn
CMD ["uvicorn", "gateway:app", "--host", "0.0.0.0", "--port", "8000"]

