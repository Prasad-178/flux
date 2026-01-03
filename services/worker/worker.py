"""
Inference Worker - The "Back of House" muscle
Consumes jobs from Redis queue and runs llama.cpp CLI for inference
Streams tokens back via Redis Pub/Sub

Uses native llama.cpp binary (compiled from source) for best performance
"""

import os
import json
import signal
import subprocess
import sys
import re
from typing import Optional

import redis

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
QUEUE_NAME = os.getenv("QUEUE_NAME", "llm_queue")
CHANNEL_PREFIX = os.getenv("CHANNEL_PREFIX", "task_channel")

# Model & llama.cpp configuration
MODEL_PATH = os.getenv("MODEL_PATH", "/models/Qwen3-1.7B-Q8_0.gguf")
LLAMA_BIN_PATH = os.getenv("LLAMA_BIN_PATH", "/opt/llama.cpp/build/bin")
N_CTX = int(os.getenv("N_CTX", 512))
N_THREADS = int(os.getenv("N_THREADS", 4))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", 0))  # 0 for CPU-only

# Global flag for graceful shutdown
running = True
current_process: Optional[subprocess.Popen] = None


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global running, current_process
    print(f"\n🛑 Received signal {signum}, shutting down gracefully...")
    running = False
    
    if current_process and current_process.poll() is None:
        current_process.terminate()
        try:
            current_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            current_process.kill()


def verify_llama_binary() -> bool:
    """Verify llama-completion binary exists and is executable"""
    binary_path = f"{LLAMA_BIN_PATH}/llama-completion"
    if not os.path.exists(binary_path):
        print(f"❌ llama-completion not found at {binary_path}")
        return False
    
    try:
        result = subprocess.run(
            [f"{LLAMA_BIN_PATH}/llama-cli", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"✅ llama.cpp version: {result.stdout.strip() or result.stderr.strip()}")
        return True
    except Exception as e:
        print(f"❌ Failed to run llama-cli: {e}")
        return False


def verify_model() -> bool:
    """Verify model file exists"""
    if not os.path.exists(MODEL_PATH):
        print(f"❌ Model not found at {MODEL_PATH}")
        return False
    
    size_gb = os.path.getsize(MODEL_PATH) / (1024 ** 3)
    print(f"✅ Model found: {MODEL_PATH} ({size_gb:.2f} GB)")
    return True


def connect_redis() -> redis.Redis:
    """Create Redis connection"""
    print(f"🔗 Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}...")
    
    client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
    )
    
    client.ping()
    print("✅ Redis connection established!")
    return client


def build_llama_command(prompt: str, max_tokens: int, temperature: float) -> list:
    """Build the llama-completion command with arguments"""
    
    # Format prompt for Qwen3 (ChatML format)
    chat_prompt = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
    
    cmd = [
        f"{LLAMA_BIN_PATH}/llama-completion",
        "-m", MODEL_PATH,
        "-p", chat_prompt,
        "-n", str(max_tokens),
        "-c", str(N_CTX),
        "-t", str(N_THREADS),
        "--temp", str(temperature),
        "-ngl", str(N_GPU_LAYERS),
        "--no-display-prompt",
        "-e",
    ]
    
    return cmd


def filter_output(text: str) -> str:
    """Filter out thinking blocks and unwanted text"""
    # Remove <think>...</think> blocks
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    # Remove EOF markers
    text = re.sub(r'EOF by user', '', text, flags=re.IGNORECASE)
    text = re.sub(r'> EOF', '', text, flags=re.IGNORECASE)
    return text


def stream_inference(
    redis_client: redis.Redis,
    request_id: str,
    prompt: str,
    max_tokens: int,
    temperature: float
) -> None:
    """Run llama-completion and stream tokens via Redis Pub/Sub"""
    global current_process
    
    channel = f"{CHANNEL_PREFIX}:{request_id}"
    cmd = build_llama_command(prompt, max_tokens, temperature)
    print(f"   Running llama-completion...")
    
    try:
        # Send start signal
        redis_client.publish(channel, json.dumps({
            "type": "start",
            "request_id": request_id
        }))
        
        # Start the process
        current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        
        token_count = 0
        full_output = ""
        in_think_block = False
        
        # Read output character by character
        while True:
            char = current_process.stdout.read(1)
            if not char:
                break
            
            full_output += char
            
            # Track thinking blocks
            if "<think>" in full_output[-10:]:
                in_think_block = True
            
            if in_think_block:
                if "</think>" in full_output[-10:]:
                    in_think_block = False
                    # Clear the accumulated think block from output tracking
                continue
            
            # Publish each character (except control chars)
            if char.isprintable() or char in ['\n', '\t', ' ']:
                token_count += 1
                redis_client.publish(channel, json.dumps({
                    "type": "token",
                    "content": char,
                    "request_id": request_id
                }))
        
        current_process.wait()
        
        # Send completion signal
        redis_client.publish(channel, json.dumps({
            "type": "complete",
            "request_id": request_id,
            "token_count": token_count
        }))
        
        print(f"✅ Completed job {request_id[:8]} | Tokens: {token_count}")
        
    except Exception as e:
        print(f"❌ Inference error: {e}")
        redis_client.publish(channel, json.dumps({
            "type": "error",
            "error": str(e),
            "request_id": request_id
        }))
    finally:
        if current_process and current_process.poll() is None:
            current_process.terminate()
        current_process = None


def process_job(redis_client: redis.Redis, job_data: str) -> None:
    """Process a single inference job"""
    try:
        job = json.loads(job_data)
        request_id = job.get("request_id")
        prompt = job.get("prompt")
        max_tokens = job.get("max_tokens", 256)
        temperature = job.get("temperature", 0.7)
        
        if not request_id or not prompt:
            print(f"⚠️ Invalid job format: {job_data}")
            return
        
        print(f"🔄 Processing job {request_id[:8]}... | Prompt: {prompt[:50]}...")
        
        stream_inference(
            redis_client,
            request_id,
            prompt,
            max_tokens,
            temperature
        )
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON decode error: {e}")
    except Exception as e:
        print(f"❌ Error processing job: {e}")


def main():
    """Main worker loop"""
    global running
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 Flux Inference Worker Starting...")
    print("   Using native llama.cpp (llama-completion)")
    print("=" * 60)
    
    print(f"\n📋 Configuration:")
    print(f"   Model: {MODEL_PATH}")
    print(f"   llama.cpp: {LLAMA_BIN_PATH}")
    print(f"   Context size: {N_CTX}")
    print(f"   Threads: {N_THREADS}")
    print(f"   GPU layers: {N_GPU_LAYERS}")
    print("")
    
    if not verify_llama_binary():
        print("❌ llama.cpp verification failed. Exiting.")
        sys.exit(1)
    
    if not verify_model():
        print("❌ Model verification failed. Exiting.")
        sys.exit(1)
    
    redis_client = connect_redis()
    
    print(f"\n👂 Listening on queue: {QUEUE_NAME}")
    print("   Press Ctrl+C to stop\n")
    
    while running:
        try:
            result = redis_client.blpop(QUEUE_NAME, timeout=1)
            
            if result:
                _, job_data = result
                process_job(redis_client, job_data)
                
        except redis.ConnectionError as e:
            print(f"❌ Redis connection lost: {e}")
            print("🔄 Attempting to reconnect in 5 seconds...")
            import time
            time.sleep(5)
            try:
                redis_client = connect_redis()
            except Exception:
                pass
        except Exception as e:
            print(f"❌ Unexpected error in main loop: {e}")
    
    print("\n👋 Worker shutdown complete.")


if __name__ == "__main__":
    main()
