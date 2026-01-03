# Inference Worker Dockerfile
# Builds llama.cpp from source for native CLI inference

FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    libopenblas-dev \
    libcurl4-openssl-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Clone and build llama.cpp
WORKDIR /build
RUN git clone --depth 1 https://github.com/ggerganov/llama.cpp.git

WORKDIR /build/llama.cpp
RUN cmake -B build \
    -DGGML_BLAS=ON \
    -DGGML_BLAS_VENDOR=OpenBLAS \
    -DLLAMA_CURL=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --config Release -j$(nproc)

# ============================================
# Runtime stage
# ============================================
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    libopenblas0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy llama.cpp binaries and libraries from builder
COPY --from=builder /build/llama.cpp/build/bin /opt/llama.cpp/build/bin
COPY --from=builder /build/llama.cpp/build/src /opt/llama.cpp/build/src
COPY --from=builder /build/llama.cpp/build/ggml/src /opt/llama.cpp/build/ggml/src
COPY --from=builder /build/llama.cpp/build/common /opt/llama.cpp/build/common

# Set library path
ENV LD_LIBRARY_PATH=/opt/llama.cpp/build/src:/opt/llama.cpp/build/ggml/src:/opt/llama.cpp/build/common:$LD_LIBRARY_PATH

# Make binaries executable
RUN chmod +x /opt/llama.cpp/build/bin/* || true

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy worker code
COPY worker.py .

# Create models directory
RUN mkdir -p /models

# Environment variables with defaults
ENV REDIS_HOST=redis \
    REDIS_PORT=6379 \
    QUEUE_NAME=llm_queue \
    CHANNEL_PREFIX=task_channel \
    MODEL_PATH=/models/Qwen3-1.7B-Q8_0.gguf \
    LLAMA_CLI_PATH=/opt/llama.cpp/build/bin/llama-cli \
    N_CTX=2048 \
    N_THREADS=4 \
    N_GPU_LAYERS=0

# Run the worker
CMD ["python", "-u", "worker.py"]
