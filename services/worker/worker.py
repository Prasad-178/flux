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
import threading
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
LLAMA_CLI_PATH = os.getenv("LLAMA_CLI_PATH", "/opt/llama.cpp/build/bin/llama-cli")
N_CTX = int(os.getenv("N_CTX", 2048))
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
    
    # Terminate any running inference process
    if current_process and current_process.poll() is None:
        current_process.terminate()
        try:
            current_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            current_process.kill()


def verify_llama_cli() -> bool:
    """Verify llama-cli binary exists and is executable"""
    if not os.path.exists(LLAMA_CLI_PATH):
        print(f"❌ llama-cli not found at {LLAMA_CLI_PATH}")
        return False
    
    if not os.access(LLAMA_CLI_PATH, os.X_OK):
        print(f"❌ llama-cli at {LLAMA_CLI_PATH} is not executable")
        return False
    
    # Test run
    try:
        result = subprocess.run(
            [LLAMA_CLI_PATH, "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        print(f"✅ llama-cli version: {result.stdout.strip() or result.stderr.strip()}")
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
    
    # Test connection
    client.ping()
    print("✅ Redis connection established!")
    return client


def build_llama_command(prompt: str, max_tokens: int, temperature: float) -> list:
    """Build the llama-cli command with arguments"""
    
    # Format prompt for chat (Qwen3 uses ChatML format)
    chat_prompt = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
    
    cmd = [
        LLAMA_CLI_PATH,
        "-m", MODEL_PATH,
        "-p", chat_prompt,
        "-n", str(max_tokens),
        "-c", str(N_CTX),
        "-t", str(N_THREADS),
        "--temp", str(temperature),
        "-ngl", str(N_GPU_LAYERS),
        "--no-display-prompt",  # Don't echo the prompt
        "-cnv",  # Conversation mode
    ]
    
    return cmd


def stream_inference(
    redis_client: redis.Redis,
    request_id: str,
    prompt: str,
    max_tokens: int,
    temperature: float
) -> None:
    """Run llama-cli and stream tokens via Redis Pub/Sub"""
    global current_process
    
    channel = f"{CHANNEL_PREFIX}:{request_id}"
    
    # Build command
    cmd = build_llama_command(prompt, max_tokens, temperature)
    
    try:
        # Send start signal
        redis_client.publish(channel, json.dumps({
            "type": "start",
            "request_id": request_id
        }))
        
        # Start the process with line buffering
        current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )
        
        token_count = 0
        buffer = ""
        
        # Read character by character for true streaming
        while True:
            char = current_process.stdout.read(1)
            if not char:
                # Process ended
                break
            
            # Skip thinking tokens (Qwen3 uses <think> tags)
            if "<think>" in buffer or buffer.endswith("<"):
                buffer += char
                if "</think>" in buffer:
                    # Clear the thinking block
                    buffer = buffer.split("</think>")[-1]
                continue
            
            buffer += char
            
            # Publish each character/token
            token_count += 1
            redis_client.publish(channel, json.dumps({
                "type": "token",
                "content": char,
                "request_id": request_id
            }))
        
        # Wait for process to complete
        current_process.wait()
        
        # Check for errors
        if current_process.returncode != 0:
            stderr = current_process.stderr.read()
            if stderr and "error" in stderr.lower():
                raise RuntimeError(f"llama-cli error: {stderr}")
        
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
        current_process = None


def process_job(redis_client: redis.Redis, job_data: str) -> None:
    """Process a single inference job"""
    try:
        job = json.loads(job_data)
        request_id = job.get("request_id")
        prompt = job.get("prompt")
        max_tokens = job.get("max_tokens", 512)
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
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("🚀 Async-Scale Inference Worker Starting...")
    print("   Using native llama.cpp CLI")
    print("=" * 60)
    
    # Verify prerequisites
    print(f"\n📋 Configuration:")
    print(f"   Model: {MODEL_PATH}")
    print(f"   llama-cli: {LLAMA_CLI_PATH}")
    print(f"   Context size: {N_CTX}")
    print(f"   Threads: {N_THREADS}")
    print(f"   GPU layers: {N_GPU_LAYERS}")
    print("")
    
    if not verify_llama_cli():
        print("❌ llama-cli verification failed. Exiting.")
        sys.exit(1)
    
    if not verify_model():
        print("❌ Model verification failed. Exiting.")
        sys.exit(1)
    
    # Connect to Redis
    redis_client = connect_redis()
    
    print(f"\n👂 Listening on queue: {QUEUE_NAME}")
    print("   Press Ctrl+C to stop\n")
    
    while running:
        try:
            # Blocking pop with 1 second timeout
            # This allows checking the `running` flag periodically
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
