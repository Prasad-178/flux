#!/usr/bin/env python3
"""
Verbose streaming test - shows all tokens received including thinking blocks
"""

import asyncio
import json
import time
import sys

try:
    import aiohttp
except ImportError:
    print("❌ aiohttp not installed. Run: pip install aiohttp")
    sys.exit(1)


async def test_streaming_verbose(
    url: str = "http://localhost:8000",
    prompt: str = "Just say: Hello, world!",
    max_tokens: int = 100,
):
    """Test streaming with verbose output."""
    print("=" * 60)
    print("🔬 Verbose Streaming Test for Flux")
    print("=" * 60)
    print(f"📍 Target: {url}")
    print(f"📝 Prompt: {prompt}")
    print()

    ws_url = url.replace("http://", "ws://").replace("https://", "wss://")

    async with aiohttp.ClientSession() as session:
        try:
            print("🔌 Connecting to WebSocket...")
            async with session.ws_connect(
                f"{ws_url}/ws/generate", timeout=120
            ) as ws:
                print("✅ Connected!")
                print()

                await ws.send_json(
                    {"prompt": prompt, "max_tokens": max_tokens, "temperature": 0.7}
                )

                tokens = []
                start_time = time.time()
                first_token_time = None

                print("📥 Receiving tokens (showing raw content):")
                print("-" * 60)

                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        current_time = time.time()

                        if data.get("type") == "token":
                            token = data.get("content", "")
                            tokens.append(token)

                            if first_token_time is None:
                                first_token_time = current_time

                            # Print each token as we receive it
                            sys.stdout.write(token)
                            sys.stdout.flush()

                        elif data.get("type") == "complete":
                            break
                        elif data.get("type") == "error":
                            print()
                            print(f"❌ Error: {data.get('error')}")
                            return False
                        elif data.get("type") == "start":
                            print("🚀 Generation started...")

                    elif msg.type in (
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.ERROR,
                    ):
                        print(f"❌ WebSocket error: {msg.type}")
                        return False

                total_time = time.time() - start_time

                print()
                print("-" * 60)
                print()
                print("=" * 60)
                print("📊 Results")
                print("=" * 60)
                print(f"📝 Total tokens (characters) received: {len(tokens)}")
                if first_token_time:
                    print(
                        f"⏱️  Time to first token (TTFT): {(first_token_time - start_time) * 1000:.0f} ms"
                    )
                print(f"⏱️  Total generation time: {total_time:.2f} s")
                if len(tokens) > 0:
                    print(f"⚡ Tokens per second: {len(tokens) / total_time:.2f}")
                print()
                print("✅ Streaming test completed!")
                return True

        except Exception as e:
            print(f"❌ Error: {e}")
            return False


async def main():
    # Use a simple prompt that's less likely to trigger thinking mode
    prompts = [
        ("Just say: Hello, world!", 50),
        ("/no_think Say hello and count to 5", 150),
    ]

    for prompt, max_tokens in prompts:
        print("\n" + "=" * 70 + "\n")
        await test_streaming_verbose(prompt=prompt, max_tokens=max_tokens)


if __name__ == "__main__":
    asyncio.run(main())
