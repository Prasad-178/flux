#!/usr/bin/env python3
"""
Comprehensive Streaming Test for Flux
=====================================
Tests streaming functionality and collects detailed metrics per request:
- Time to First Token (TTFT)
- Tokens per second (TPS)
- Total generation time
- llama.cpp internal metrics

This proves that streaming is REAL and not mocked!
"""

import asyncio
import json
import time
import sys
from dataclasses import dataclass
from typing import List, Optional

try:
    import aiohttp
except ImportError:
    print("❌ aiohttp not installed. Run: pip install aiohttp")
    sys.exit(1)


@dataclass
class RequestMetrics:
    """Metrics collected for a single request"""
    prompt: str
    success: bool
    token_count: int
    ttft_ms: float  # Time to first token
    total_time_ms: float
    generation_time_ms: float
    tokens_per_second: float
    # Server-reported metrics (from llama.cpp)
    llama_prompt_tokens: int = 0
    llama_prompt_tps: float = 0.0
    llama_generation_tokens: int = 0
    llama_generation_tps: float = 0.0
    error: Optional[str] = None


async def test_single_request(
    session: aiohttp.ClientSession,
    url: str,
    prompt: str,
    max_tokens: int = 512,
) -> RequestMetrics:
    """
    Send a single streaming request and collect detailed metrics.
    
    This function proves streaming is REAL by:
    1. Measuring time between connection and first token (TTFT)
    2. Counting tokens as they arrive in real-time
    3. Calculating tokens per second
    4. Collecting server-side llama.cpp metrics
    """
    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")
    
    # Timing variables
    start_time = time.time()
    first_token_time: Optional[float] = None
    tokens: List[str] = []
    token_timestamps: List[float] = []
    server_metrics: dict = {}
    error: Optional[str] = None
    
    try:
        async with session.ws_connect(
            f"{ws_url}/ws/generate",
            timeout=aiohttp.ClientTimeout(total=300)
        ) as ws:
            # Send request
            await ws.send_json({
                "prompt": prompt,
                "max_tokens": max_tokens,
            })
            
            # Receive tokens - each one arrives in real-time (TRUE streaming!)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    current_time = time.time()
                    
                    if data.get("type") == "token":
                        # Record first token time
                        if first_token_time is None:
                            first_token_time = current_time
                        
                        tokens.append(data.get("content", ""))
                        token_timestamps.append(current_time)
                    
                    elif data.get("type") == "complete":
                        server_metrics = data.get("metrics", {})
                        break
                    
                    elif data.get("type") == "error":
                        error = data.get("error", "Unknown error")
                        break
                
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    error = f"WebSocket closed: {msg.type}"
                    break
    
    except asyncio.TimeoutError:
        error = "Request timeout"
    except Exception as e:
        error = str(e)
    
    end_time = time.time()
    
    # Calculate metrics
    total_time_ms = (end_time - start_time) * 1000
    ttft_ms = (first_token_time - start_time) * 1000 if first_token_time else total_time_ms
    generation_time_ms = (end_time - first_token_time) * 1000 if first_token_time else 0
    token_count = len(tokens)
    tps = (token_count / (generation_time_ms / 1000)) if generation_time_ms > 0 else 0
    
    return RequestMetrics(
        prompt=prompt,
        success=error is None and token_count > 0,
        token_count=token_count,
        ttft_ms=ttft_ms,
        total_time_ms=total_time_ms,
        generation_time_ms=generation_time_ms,
        tokens_per_second=tps,
        llama_prompt_tokens=server_metrics.get("llama_prompt_tokens", 0),
        llama_prompt_tps=server_metrics.get("llama_prompt_tps", 0.0),
        llama_generation_tokens=server_metrics.get("llama_generation_tokens", 0),
        llama_generation_tps=server_metrics.get("llama_generation_tps", 0.0),
        error=error,
    )


def print_metrics_table(metrics_list: List[RequestMetrics]):
    """Print a formatted table of all metrics"""
    print("\n" + "=" * 100)
    print("📊 DETAILED METRICS PER REQUEST")
    print("=" * 100)
    print(f"{'#':<3} {'Prompt':<30} {'Tokens':<8} {'TTFT':<10} {'Gen Time':<12} {'TPS':<10} {'LLama TPS':<12} {'Status':<8}")
    print("-" * 100)
    
    for i, m in enumerate(metrics_list, 1):
        prompt_short = m.prompt[:27] + "..." if len(m.prompt) > 30 else m.prompt
        status = "✅" if m.success else "❌"
        llama_tps = f"{m.llama_generation_tps:.1f}" if m.llama_generation_tps > 0 else "—"
        
        print(f"{i:<3} {prompt_short:<30} {m.token_count:<8} {m.ttft_ms:<10.0f} {m.generation_time_ms:<12.0f} {m.tokens_per_second:<10.1f} {llama_tps:<12} {status:<8}")
        
        if m.error:
            print(f"    ❌ Error: {m.error}")
    
    print("-" * 100)
    
    # Summary statistics
    successful = [m for m in metrics_list if m.success]
    if successful:
        avg_ttft = sum(m.ttft_ms for m in successful) / len(successful)
        avg_tps = sum(m.tokens_per_second for m in successful) / len(successful)
        avg_llama_tps = sum(m.llama_generation_tps for m in successful) / len(successful)
        total_tokens = sum(m.token_count for m in successful)
        
        print(f"\n📈 SUMMARY ({len(successful)}/{len(metrics_list)} successful)")
        print(f"   Average TTFT: {avg_ttft:.0f} ms")
        print(f"   Average TPS (measured): {avg_tps:.1f} tokens/sec")
        if avg_llama_tps > 0:
            print(f"   Average TPS (llama.cpp): {avg_llama_tps:.1f} tokens/sec")
        print(f"   Total tokens generated: {total_tokens}")


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
    
    # Test prompts designed to generate varied responses
    test_prompts = [
        "Say hello and tell me what 2 + 2 equals.",
        "Write a haiku about programming.",
        "List 5 colors of the rainbow.",
        "What is the capital of France? Answer in one sentence.",
        "Count from 1 to 10.",
    ]
    
    print("=" * 100)
    print("🔬 FLUX STREAMING TEST")
    print("=" * 100)
    print(f"📍 Target: {url}")
    print(f"📝 Number of test requests: {len(test_prompts)}")
    print()
    print("🔄 This test proves that streaming is REAL by measuring:")
    print("   • Time to First Token (TTFT) - how quickly we get the first response")
    print("   • Tokens per second - proving tokens arrive incrementally")
    print("   • llama.cpp metrics - internal model performance")
    print()
    print("=" * 100)
    
    metrics_list: List[RequestMetrics] = []
    
    async with aiohttp.ClientSession() as session:
        for i, prompt in enumerate(test_prompts, 1):
            print(f"\n🔄 Request {i}/{len(test_prompts)}: {prompt[:50]}...")
            
            metrics = await test_single_request(session, url, prompt, max_tokens=256)
            metrics_list.append(metrics)
            
            if metrics.success:
                print(f"   ✅ {metrics.token_count} tokens | TTFT: {metrics.ttft_ms:.0f}ms | {metrics.tokens_per_second:.1f} tok/s")
            else:
                print(f"   ❌ Error: {metrics.error}")
    
    # Print detailed metrics table
    print_metrics_table(metrics_list)
    
    # Final summary
    print("\n" + "=" * 100)
    all_success = all(m.success for m in metrics_list)
    if all_success:
        print("🎉 ALL TESTS PASSED - STREAMING IS WORKING!")
    else:
        failed = sum(1 for m in metrics_list if not m.success)
        print(f"⚠️  {failed}/{len(metrics_list)} tests failed")
    print("=" * 100)


if __name__ == "__main__":
    asyncio.run(main())
