"""
Inference Worker - The "Back of House" muscle
Consumes jobs from Redis queue and runs llama.cpp CLI for inference
Streams tokens back via Redis Pub/Sub

Uses native llama.cpp binary (compiled from source) for best performance

STREAMING ARCHITECTURE:
=======================
1. Gateway pushes job to Redis queue (RPUSH)
2. Worker pops job from queue (BLPOP)
3. Worker runs llama-completion subprocess
4. Worker reads stdout CHARACTER BY CHARACTER (real-time!)
5. Each character is published to Redis Pub/Sub channel
6. Gateway subscribes to that channel and forwards to WebSocket
7. Browser receives each token via WebSocket in real-time

This is TRUE streaming - no buffering, no mocking!
"""

import os
import json
import signal
import subprocess
import sys
import re
import time
from typing import Optional
from dataclasses import dataclass

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
N_CTX = int(os.getenv("N_CTX", 4096))  # Increased context size
N_THREADS = int(os.getenv("N_THREADS", 4))
N_GPU_LAYERS = int(os.getenv("N_GPU_LAYERS", 0))  # 0 for CPU-only

# Global flag for graceful shutdown
running = True
current_process: Optional[subprocess.Popen] = None


@dataclass
class LlamaMetrics:
    """Metrics parsed from llama.cpp stderr output"""
    prompt_tokens: int = 0
    generated_tokens: int = 0
    prompt_eval_time_ms: float = 0.0
    generation_time_ms: float = 0.0
    prompt_tps: float = 0.0  # tokens per second for prompt processing
    generation_tps: float = 0.0  # tokens per second for generation
    total_time_ms: float = 0.0


def parse_llama_metrics(stderr_output: str) -> LlamaMetrics:
    """Parse performance metrics from llama.cpp stderr output"""
    metrics = LlamaMetrics()
    
    # Example llama.cpp output:
    # llama_print_timings:        load time =    1234.56 ms
    # llama_print_timings:      sample time =      12.34 ms /    50 runs   (    0.25 ms per token,  4050.00 tokens per second)
    # llama_print_timings: prompt eval time =     567.89 ms /    25 tokens (   22.72 ms per token,    44.02 tokens per second)
    # llama_print_timings:        eval time =    2345.67 ms /    49 runs   (   47.87 ms per token,    20.89 tokens per second)
    # llama_print_timings:       total time =    3456.78 ms /    74 tokens
    
    # Parse prompt eval (prompt processing)
    prompt_match = re.search(
        r'prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens.*?([\d.]+)\s*tokens per second',
        stderr_output
    )
    if prompt_match:
        metrics.prompt_eval_time_ms = float(prompt_match.group(1))
        metrics.prompt_tokens = int(prompt_match.group(2))
        metrics.prompt_tps = float(prompt_match.group(3))
    
    # Parse eval (generation)
    eval_match = re.search(
        r'eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*runs.*?([\d.]+)\s*tokens per second',
        stderr_output
    )
    if eval_match:
        metrics.generation_time_ms = float(eval_match.group(1))
        metrics.generated_tokens = int(eval_match.group(2))
        metrics.generation_tps = float(eval_match.group(3))
    
    # Parse total time
    total_match = re.search(r'total time\s*=\s*([\d.]+)\s*ms', stderr_output)
    if total_match:
        metrics.total_time_ms = float(total_match.group(1))
    
    return metrics


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


def build_llama_command(prompt: str, max_tokens: int) -> list:
    """Build the llama-completion command with minimal, clean arguments"""
    
    # Format prompt for Qwen3 (ChatML format)
    # Add /no_think to disable thinking mode for direct responses
    chat_prompt = f"<|im_start|>user\n/no_think {prompt}<|im_end|>\n<|im_start|>assistant\n"
    
    cmd = [
        f"{LLAMA_BIN_PATH}/llama-completion",
        "-m", MODEL_PATH,
        "-p", chat_prompt,
        "-n", str(max_tokens),           # Max tokens to generate
        "-c", str(N_CTX),                 # Context window
        "-t", str(N_THREADS),             # CPU threads
        "-ngl", str(N_GPU_LAYERS),        # GPU layers (0 = CPU only)
        "--temp", "0.0",                  # Deterministic output (greedy)
        "--no-display-prompt",            # Don't echo prompt
        "-e",                             # Escape special chars
    ]
    
    return cmd


def stream_inference(
    redis_client: redis.Redis,
    request_id: str,
    prompt: str,
    max_tokens: int,
) -> None:
    """
    Run llama-completion and stream tokens via Redis Pub/Sub.
    
    THIS IS TRUE STREAMING:
    - We read stdout CHARACTER BY CHARACTER
    - Each character is published immediately to Redis Pub/Sub
    - Gateway subscribes and forwards to WebSocket
    - No buffering, no mocking!
    """
    global current_process
    
    channel = f"{CHANNEL_PREFIX}:{request_id}"
    cmd = build_llama_command(prompt, max_tokens)
    
    # Timing metrics
    job_start_time = time.time()
    first_token_time = None
    
    print(f"   Running llama-completion...")
    
    try:
        # Send start signal with timing
        redis_client.publish(channel, json.dumps({
            "type": "start",
            "request_id": request_id,
            "timestamp": time.time()
        }))
        
        # Start the process - capture both stdout (tokens) and stderr (metrics)
        current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,  # Line buffered (but we read char-by-char)
        )
        
        token_count = 0
        full_output = ""
        stderr_output = ""
        
        # Read output character by character - THIS IS REAL STREAMING!
        while True:
            char = current_process.stdout.read(1)
            if not char:
                break
            
            full_output += char
            
            # Record time to first token
            if first_token_time is None and char.strip():
                first_token_time = time.time()
            
            # Publish each character immediately via Redis Pub/Sub
            # The gateway subscribes to this channel and forwards to WebSocket
            if char.isprintable() or char in ['\n', '\t', ' ']:
                token_count += 1
                redis_client.publish(channel, json.dumps({
                    "type": "token",
                    "content": char,
                    "request_id": request_id,
                    "token_index": token_count,
                    "timestamp": time.time()
                }))
        
        # Wait for process to complete and capture stderr
        current_process.wait()
        stderr_output = current_process.stderr.read()
        
        # Calculate timing
        end_time = time.time()
        total_time = end_time - job_start_time
        ttft = (first_token_time - job_start_time) if first_token_time else 0
        generation_time = (end_time - first_token_time) if first_token_time else total_time
        
        # Parse llama.cpp metrics from stderr
        llama_metrics = parse_llama_metrics(stderr_output)
        
        # Calculate our own metrics
        tokens_per_second = token_count / generation_time if generation_time > 0 else 0
        
        # Send completion signal with detailed metrics
        completion_data = {
            "type": "complete",
            "request_id": request_id,
            "token_count": token_count,
            "metrics": {
                "ttft_ms": round(ttft * 1000, 2),
                "total_time_ms": round(total_time * 1000, 2),
                "generation_time_ms": round(generation_time * 1000, 2),
                "tokens_per_second": round(tokens_per_second, 2),
                # llama.cpp internal metrics
                "llama_prompt_tokens": llama_metrics.prompt_tokens,
                "llama_prompt_eval_ms": llama_metrics.prompt_eval_time_ms,
                "llama_prompt_tps": round(llama_metrics.prompt_tps, 2),
                "llama_generation_tokens": llama_metrics.generated_tokens,
                "llama_generation_ms": llama_metrics.generation_time_ms,
                "llama_generation_tps": round(llama_metrics.generation_tps, 2),
            }
        }
        redis_client.publish(channel, json.dumps(completion_data))
        
        # Log detailed metrics
        print(f"✅ Completed job {request_id[:8]}")
        print(f"   📊 Tokens: {token_count} | TTFT: {ttft*1000:.0f}ms | Total: {total_time*1000:.0f}ms")
        print(f"   ⚡ Generation: {tokens_per_second:.1f} tok/s")
        if llama_metrics.prompt_tps > 0:
            print(f"   📥 Prompt eval: {llama_metrics.prompt_tokens} tokens @ {llama_metrics.prompt_tps:.1f} tok/s")
        if llama_metrics.generation_tps > 0:
            print(f"   📤 Generation: {llama_metrics.generated_tokens} tokens @ {llama_metrics.generation_tps:.1f} tok/s")
        
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
        max_tokens = job.get("max_tokens", 2048)  # Increased default
        
        if not request_id or not prompt:
            print(f"⚠️ Invalid job format: {job_data}")
            return
        
        print(f"🔄 Processing job {request_id[:8]}... | Prompt: {prompt[:50]}...")
        
        stream_inference(
            redis_client,
            request_id,
            prompt,
            max_tokens,
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
    print(f"   Temperature: 0.0 (deterministic)")
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
