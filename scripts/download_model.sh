#!/bin/bash
# Download the Qwen 3 1.7B quantized model

set -e

MODEL_DIR="./models"
MODEL_FILE="Qwen3-1.7B-Q8_0.gguf"
MODEL_URL="https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q8_0.gguf"

echo "=============================================="
echo "  Async-Scale Model Downloader"
echo "=============================================="
echo ""

# Create models directory if it doesn't exist
mkdir -p "$MODEL_DIR"

# Check if model already exists
if [ -f "$MODEL_DIR/$MODEL_FILE" ]; then
    echo "✅ Model already exists at $MODEL_DIR/$MODEL_FILE"
    echo "   Size: $(du -h "$MODEL_DIR/$MODEL_FILE" | cut -f1)"
    exit 0
fi

echo "📦 Downloading model: $MODEL_FILE"
echo "   From: $MODEL_URL"
echo "   To: $MODEL_DIR/$MODEL_FILE"
echo ""
echo "⚠️  This may take a while (~1.8GB download)..."
echo ""

# Download with progress bar
if command -v wget &> /dev/null; then
    wget -O "$MODEL_DIR/$MODEL_FILE" "$MODEL_URL" --show-progress
elif command -v curl &> /dev/null; then
    curl -L -o "$MODEL_DIR/$MODEL_FILE" "$MODEL_URL" --progress-bar
else
    echo "❌ Error: Neither wget nor curl found. Please install one of them."
    exit 1
fi

echo ""
echo "✅ Model downloaded successfully!"
echo "   Location: $MODEL_DIR/$MODEL_FILE"
echo "   Size: $(du -h "$MODEL_DIR/$MODEL_FILE" | cut -f1)"

