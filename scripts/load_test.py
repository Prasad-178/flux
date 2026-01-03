#!/usr/bin/env python3
"""
Load Testing Script for Flux
Floods the queue with requests to trigger KEDA autoscaling
"""

import asyncio
import json
import time
import argparse
from typing import Optional

try:
    import aiohttp
except ImportError:
    print("❌ aiohttp not installed. Run: uv add aiohttp")
    exit(1)


# Test prompts
PROMPTS = [
    "What is the capital of France?",
    "Explain quantum computing in simple terms.",
    "Write a haiku about programming.",
    "What are the benefits of exercise?",
    "How does photosynthesis work?",
    "Describe the water cycle.",
    "What is machine learning?",
    "Explain the theory of relativity.",
    "How do computers work?",
    "What is the meaning of life?",
]


async def send_request(
    session: aiohttp.ClientSession,
    url: str,
    prompt: str,
    request_id: int,
    max_tokens: int = 100,
    stream: bool = True,
) -> dict:
    """Send a single WebSocket request"""
    start_time = time.time()
    tokens = 0
    status = "success"
    error = None
    
    try:
        if stream:
            # WebSocket streaming
            async with session.ws_connect(f"{url}/ws/generate", timeout=120) as ws:
                await ws.send_json({
                    "prompt": prompt,
                    "max_tokens": max_tokens,
                    "temperature": 0.7
                })
                
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("type") == "token":
                            tokens += 1
                        elif data.get("type") == "complete":
                            break
                        elif data.get("type") == "error":
                            status = "error"
                            error = data.get("error")
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        status = "error"
                        break
        else:
            # REST API (just queue the job)
            async with session.post(
                f"{url}/api/generate",
                json={"prompt": prompt, "max_tokens": max_tokens}
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    status = "error"
                    error = str(data)
                    
    except asyncio.TimeoutError:
        status = "timeout"
    except Exception as e:
        status = "error"
        error = str(e)
    
    elapsed = time.time() - start_time
    
    return {
        "request_id": request_id,
        "status": status,
        "tokens": tokens,
        "elapsed": elapsed,
        "error": error,
    }


async def run_load_test(
    url: str,
    num_requests: int,
    concurrency: int,
    max_tokens: int,
    stream: bool,
):
    """Run the load test"""
    print("=" * 60)
    print("⚡ Flux Load Tester")
    print("=" * 60)
    print(f"📍 Target: {url}")
    print(f"📊 Requests: {num_requests}")
    print(f"🔄 Concurrency: {concurrency}")
    print(f"📝 Max tokens: {max_tokens}")
    print(f"🌊 Streaming: {stream}")
    print("=" * 60)
    print()
    
    results = []
    semaphore = asyncio.Semaphore(concurrency)
    
    async def limited_request(session, i):
        async with semaphore:
            prompt = PROMPTS[i % len(PROMPTS)]
            result = await send_request(
                session, url, prompt, i, max_tokens, stream
            )
            
            status_icon = "✅" if result["status"] == "success" else "❌"
            print(f"{status_icon} Request {i+1}/{num_requests} | "
                  f"Status: {result['status']} | "
                  f"Tokens: {result['tokens']} | "
                  f"Time: {result['elapsed']:.2f}s")
            
            return result
    
    start_time = time.time()
    
    async with aiohttp.ClientSession() as session:
        tasks = [limited_request(session, i) for i in range(num_requests)]
        results = await asyncio.gather(*tasks)
    
    total_time = time.time() - start_time
    
    # Calculate stats
    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] != "success"]
    
    total_tokens = sum(r["tokens"] for r in successful)
    avg_latency = sum(r["elapsed"] for r in successful) / len(successful) if successful else 0
    
    print()
    print("=" * 60)
    print("📊 Results Summary")
    print("=" * 60)
    print(f"✅ Successful: {len(successful)}/{num_requests}")
    print(f"❌ Failed: {len(failed)}/{num_requests}")
    print(f"⏱️  Total time: {total_time:.2f}s")
    print(f"📈 Throughput: {num_requests/total_time:.2f} req/s")
    print(f"📝 Total tokens: {total_tokens}")
    print(f"⚡ Tokens/sec: {total_tokens/total_time:.2f}")
    print(f"📉 Avg latency: {avg_latency:.2f}s")
    
    if failed:
        print()
        print("❌ Failed requests:")
        for r in failed[:5]:  # Show first 5 failures
            print(f"   Request {r['request_id']}: {r['status']} - {r['error']}")
    
    print("=" * 60)


async def flood_queue(url: str, num_requests: int):
    """Flood the queue without waiting (for KEDA testing)"""
    print("=" * 60)
    print("🌊 Queue Flood Mode - KEDA Autoscaling Test")
    print("=" * 60)
    print(f"📍 Target: {url}")
    print(f"📊 Requests to queue: {num_requests}")
    print("=" * 60)
    print()
    print("🚀 Flooding queue...")
    
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i in range(num_requests):
            prompt = PROMPTS[i % len(PROMPTS)]
            tasks.append(
                session.post(
                    f"{url}/api/generate",
                    json={"prompt": prompt, "max_tokens": 50}
                )
            )
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if not isinstance(r, Exception))
        
    print(f"✅ Queued {success}/{num_requests} requests")
    print()
    print("📊 Monitor KEDA scaling with:")
    print("   kubectl get pods -n Flux -w")
    print("   kubectl get scaledobject -n Flux")
    print()
    print("📈 Check queue length:")
    print(f"   curl {url}/queue/status")


def main():
    parser = argparse.ArgumentParser(description="Flux Load Tester")
    parser.add_argument(
        "--url",
        default="http://localhost:8000",
        help="Gateway URL (default: http://localhost:8000)"
    )
    parser.add_argument(
        "-n", "--num-requests",
        type=int,
        default=10,
        help="Number of requests (default: 10)"
    )
    parser.add_argument(
        "-c", "--concurrency",
        type=int,
        default=5,
        help="Concurrent requests (default: 5)"
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=100,
        help="Max tokens per request (default: 100)"
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Use REST API instead of WebSocket"
    )
    parser.add_argument(
        "--flood",
        action="store_true",
        help="Flood mode: queue requests without waiting (for KEDA testing)"
    )
    
    args = parser.parse_args()
    
    if args.flood:
        asyncio.run(flood_queue(args.url, args.num_requests))
    else:
        asyncio.run(run_load_test(
            args.url,
            args.num_requests,
            args.concurrency,
            args.max_tokens,
            not args.no_stream
        ))


if __name__ == "__main__":
    main()

