"""
API Gateway - The "Front of House"
Handles WebSocket connections and Redis Pub/Sub for real-time streaming
Exposes Prometheus metrics for observability
"""

import os
import json
import uuid
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import redis.asyncio as aioredis
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
QUEUE_NAME = os.getenv("QUEUE_NAME", "llm_queue")
CHANNEL_PREFIX = os.getenv("CHANNEL_PREFIX", "task_channel")

# Global Redis connection pool
redis_pool: Optional[aioredis.ConnectionPool] = None

# ============================================
# Prometheus Metrics
# ============================================
REQUEST_COUNT = Counter(
    "gateway_requests_total",
    "Total number of inference requests",
    ["status"]  # success, error
)

ACTIVE_CONNECTIONS = Gauge(
    "gateway_active_websocket_connections",
    "Number of active WebSocket connections"
)

QUEUE_LENGTH = Gauge(
    "gateway_redis_queue_length",
    "Current length of the Redis job queue"
)

REQUEST_LATENCY = Histogram(
    "gateway_request_latency_seconds",
    "Time from request to completion",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0]
)

TOKENS_GENERATED = Counter(
    "gateway_tokens_generated_total",
    "Total number of tokens generated"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global redis_pool
    
    print("🚀 Starting Async-Scale Gateway...")
    print(f"🔗 Connecting to Redis at {REDIS_HOST}:{REDIS_PORT}")
    
    # Create Redis connection pool
    redis_pool = aioredis.ConnectionPool.from_url(
        f"redis://{':' + REDIS_PASSWORD + '@' if REDIS_PASSWORD else ''}{REDIS_HOST}:{REDIS_PORT}",
        decode_responses=True,
    )
    
    # Start background task to update queue length metric
    async def update_queue_metrics():
        while True:
            try:
                client = aioredis.Redis(connection_pool=redis_pool)
                length = await client.llen(QUEUE_NAME)
                QUEUE_LENGTH.set(length)
            except Exception:
                pass
            await asyncio.sleep(5)
    
    metrics_task = asyncio.create_task(update_queue_metrics())
    
    print("✅ Gateway ready!")
    yield
    
    # Cleanup
    print("🛑 Shutting down Gateway...")
    metrics_task.cancel()
    if redis_pool:
        await redis_pool.disconnect()


app = FastAPI(
    title="Async-Scale Gateway",
    description="Event-driven LLM Orchestration API",
    version="1.0.0",
    lifespan=lifespan,
)


class GenerateRequest(BaseModel):
    """Request model for text generation"""
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.7


def get_redis() -> aioredis.Redis:
    """Get Redis client from pool"""
    return aioredis.Redis(connection_pool=redis_pool)


# Simple HTML client for testing
TEST_CLIENT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Async-Scale LLM Chat</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            background: linear-gradient(135deg, #0f0f23 0%, #1a1a2e 50%, #16213e 100%);
            min-height: 100vh;
            color: #e4e4e7;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }
        header {
            text-align: center;
            padding: 2rem 0;
            border-bottom: 1px solid #3f3f46;
            margin-bottom: 2rem;
        }
        h1 {
            font-size: 2.5rem;
            background: linear-gradient(90deg, #06b6d4, #8b5cf6, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 0.5rem;
        }
        .subtitle {
            color: #71717a;
            font-size: 0.9rem;
        }
        .chat-container {
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }
        #output {
            flex: 1;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid #3f3f46;
            border-radius: 12px;
            padding: 1.5rem;
            overflow-y: auto;
            min-height: 400px;
            font-size: 0.95rem;
            line-height: 1.7;
        }
        .message {
            margin-bottom: 1.5rem;
            padding: 1rem;
            border-radius: 8px;
        }
        .user-msg {
            background: rgba(139, 92, 246, 0.15);
            border-left: 3px solid #8b5cf6;
        }
        .assistant-msg {
            background: rgba(6, 182, 212, 0.1);
            border-left: 3px solid #06b6d4;
        }
        .msg-label {
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
            opacity: 0.7;
        }
        .input-area {
            display: flex;
            gap: 1rem;
        }
        textarea {
            flex: 1;
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid #3f3f46;
            border-radius: 8px;
            padding: 1rem;
            color: #e4e4e7;
            font-family: inherit;
            font-size: 0.95rem;
            resize: none;
            transition: border-color 0.2s;
        }
        textarea:focus {
            outline: none;
            border-color: #8b5cf6;
        }
        button {
            background: linear-gradient(135deg, #8b5cf6 0%, #06b6d4 100%);
            border: none;
            border-radius: 8px;
            padding: 1rem 2rem;
            color: white;
            font-family: inherit;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.2s, opacity 0.2s;
        }
        button:hover:not(:disabled) {
            transform: translateY(-2px);
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .status {
            text-align: center;
            padding: 0.5rem;
            font-size: 0.8rem;
            color: #71717a;
        }
        .status.connected { color: #22c55e; }
        .status.error { color: #ef4444; }
        .cursor {
            display: inline-block;
            width: 8px;
            height: 1em;
            background: #06b6d4;
            animation: blink 1s infinite;
            vertical-align: text-bottom;
        }
        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0; }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>⚡ Async-Scale</h1>
            <p class="subtitle">Event-Driven LLM Orchestration • Real-Time Token Streaming</p>
        </header>
        
        <div class="chat-container">
            <div id="output"></div>
            <div class="input-area">
                <textarea id="prompt" rows="3" placeholder="Enter your prompt..."></textarea>
                <button id="send" onclick="sendMessage()">Generate</button>
            </div>
            <div id="status" class="status">Disconnected</div>
        </div>
    </div>

    <script>
        let ws = null;
        const output = document.getElementById('output');
        const promptInput = document.getElementById('prompt');
        const sendBtn = document.getElementById('send');
        const status = document.getElementById('status');
        let currentResponse = null;

        function updateStatus(text, className = '') {
            status.textContent = text;
            status.className = 'status ' + className;
        }

        function addUserMessage(text) {
            const div = document.createElement('div');
            div.className = 'message user-msg';
            div.innerHTML = `<div class="msg-label">You</div><div>${escapeHtml(text)}</div>`;
            output.appendChild(div);
            output.scrollTop = output.scrollHeight;
        }

        function startAssistantMessage() {
            const div = document.createElement('div');
            div.className = 'message assistant-msg';
            div.innerHTML = `<div class="msg-label">Assistant</div><div class="content"><span class="cursor"></span></div>`;
            output.appendChild(div);
            currentResponse = div.querySelector('.content');
            output.scrollTop = output.scrollHeight;
        }

        function appendToken(token) {
            if (currentResponse) {
                const cursor = currentResponse.querySelector('.cursor');
                if (cursor) cursor.remove();
                currentResponse.innerHTML += escapeHtml(token) + '<span class="cursor"></span>';
                output.scrollTop = output.scrollHeight;
            }
        }

        function endAssistantMessage() {
            if (currentResponse) {
                const cursor = currentResponse.querySelector('.cursor');
                if (cursor) cursor.remove();
                currentResponse = null;
            }
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        function sendMessage() {
            const prompt = promptInput.value.trim();
            if (!prompt) return;

            sendBtn.disabled = true;
            promptInput.disabled = true;
            addUserMessage(prompt);
            promptInput.value = '';

            // Connect WebSocket
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws/generate`);

            ws.onopen = () => {
                updateStatus('Connected • Generating...', 'connected');
                startAssistantMessage();
                ws.send(JSON.stringify({
                    prompt: prompt,
                    max_tokens: 512,
                    temperature: 0.7
                }));
            };

            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.type === 'token') {
                    appendToken(data.content);
                } else if (data.type === 'complete') {
                    endAssistantMessage();
                    updateStatus(`Completed • ${data.token_count} tokens`, 'connected');
                } else if (data.type === 'error') {
                    endAssistantMessage();
                    updateStatus(`Error: ${data.error}`, 'error');
                }
            };

            ws.onclose = () => {
                sendBtn.disabled = false;
                promptInput.disabled = false;
                if (status.textContent.includes('Generating')) {
                    updateStatus('Disconnected');
                }
            };

            ws.onerror = (error) => {
                updateStatus('Connection error', 'error');
                sendBtn.disabled = false;
                promptInput.disabled = false;
            };
        }

        // Enter to send (Shift+Enter for newline)
        promptInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });

        updateStatus('Ready to connect');
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the test client HTML"""
    return TEST_CLIENT_HTML


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        redis_client = get_redis()
        await redis_client.ping()
        return {"status": "healthy", "redis": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Unhealthy: {str(e)}")


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/queue/status")
async def queue_status():
    """Get current queue status"""
    redis_client = get_redis()
    queue_length = await redis_client.llen(QUEUE_NAME)
    return {
        "queue_name": QUEUE_NAME,
        "pending_jobs": queue_length
    }


@app.websocket("/ws/generate")
async def websocket_generate(websocket: WebSocket):
    """
    WebSocket endpoint for streaming LLM generation
    
    Flow:
    1. Accept WebSocket connection
    2. Receive generation request
    3. Push job to Redis queue
    4. Subscribe to task channel
    5. Stream tokens back to client
    """
    await websocket.accept()
    ACTIVE_CONNECTIONS.inc()
    
    request_id = str(uuid.uuid4())
    redis_client = get_redis()
    pubsub = redis_client.pubsub()
    start_time = time.time()
    token_count = 0
    
    try:
        # Receive the generation request
        data = await websocket.receive_text()
        request = json.loads(data)
        
        prompt = request.get("prompt", "")
        max_tokens = request.get("max_tokens", 512)
        temperature = request.get("temperature", 0.7)
        
        if not prompt:
            await websocket.send_json({"type": "error", "error": "Prompt is required"})
            REQUEST_COUNT.labels(status="error").inc()
            return
        
        # Create job payload
        job = {
            "request_id": request_id,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Subscribe to the response channel BEFORE pushing the job
        channel = f"{CHANNEL_PREFIX}:{request_id}"
        await pubsub.subscribe(channel)
        
        # Push job to the queue
        await redis_client.rpush(QUEUE_NAME, json.dumps(job))
        
        # Stream responses from the worker
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                await websocket.send_json(data)
                
                # Track tokens
                if data.get("type") == "token":
                    token_count += 1
                
                # End stream on complete or error
                if data.get("type") == "complete":
                    REQUEST_COUNT.labels(status="success").inc()
                    TOKENS_GENERATED.inc(data.get("token_count", token_count))
                    REQUEST_LATENCY.observe(time.time() - start_time)
                    break
                elif data.get("type") == "error":
                    REQUEST_COUNT.labels(status="error").inc()
                    break
                    
    except WebSocketDisconnect:
        print(f"🔌 Client disconnected: {request_id[:8]}")
        REQUEST_COUNT.labels(status="disconnect").inc()
    except json.JSONDecodeError as e:
        await websocket.send_json({"type": "error", "error": f"Invalid JSON: {str(e)}"})
        REQUEST_COUNT.labels(status="error").inc()
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        REQUEST_COUNT.labels(status="error").inc()
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
        except Exception:
            pass
    finally:
        ACTIVE_CONNECTIONS.dec()
        # Cleanup
        try:
            await pubsub.unsubscribe(f"{CHANNEL_PREFIX}:{request_id}")
            await pubsub.close()
        except Exception:
            pass


# REST API fallback (non-streaming)
@app.post("/api/generate")
async def api_generate(request: GenerateRequest):
    """
    REST API endpoint for generation (non-streaming)
    Returns immediately with request_id for polling
    """
    request_id = str(uuid.uuid4())
    redis_client = get_redis()
    
    job = {
        "request_id": request_id,
        "prompt": request.prompt,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }
    
    await redis_client.rpush(QUEUE_NAME, json.dumps(job))
    
    return {
        "request_id": request_id,
        "status": "queued",
        "message": "Job queued for processing. Use WebSocket endpoint for streaming."
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
