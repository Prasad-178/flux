"""
API Gateway - The "Front of House"
Handles WebSocket connections and Redis Pub/Sub for real-time streaming
Exposes Prometheus metrics for observability

STREAMING ARCHITECTURE - THIS IS NOT MOCKED!
=============================================
1. Client connects via WebSocket to /ws/generate
2. Gateway receives prompt and creates a unique request_id
3. Gateway subscribes to Redis Pub/Sub channel for that request_id
4. Gateway pushes job to Redis queue (worker will pop it)
5. Worker runs llama.cpp and publishes each token to Redis Pub/Sub
6. Gateway receives each token from Redis Pub/Sub
7. Gateway forwards each token to client via WebSocket
8. This is TRUE streaming - each token arrives as it's generated!
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
DEFAULT_MAX_TOKENS = int(os.getenv("DEFAULT_MAX_TOKENS", 2048))

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

TTFT = Histogram(
    "gateway_time_to_first_token_seconds",
    "Time to first token",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]
)

TOKENS_GENERATED = Counter(
    "gateway_tokens_generated_total",
    "Total number of tokens generated"
)

TOKENS_PER_SECOND = Histogram(
    "gateway_tokens_per_second",
    "Tokens generated per second",
    buckets=[1, 5, 10, 20, 50, 100, 200]
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle"""
    global redis_pool
    
    print("🚀 Starting Flux Gateway...")
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
    title="Flux Gateway",
    description="Event-driven LLM Orchestration API with Real-Time Streaming",
    version="2.0.0",
    lifespan=lifespan,
)


class GenerateRequest(BaseModel):
    """Request model for text generation"""
    prompt: str
    max_tokens: int = DEFAULT_MAX_TOKENS


def get_redis() -> aioredis.Redis:
    """Get Redis client from pool"""
    return aioredis.Redis(connection_pool=redis_pool)


# Modern Claude-like UI
TEST_CLIENT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Flux • AI Assistant</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        
        :root {
            --bg-primary: #1a1a1a;
            --bg-secondary: #2a2a2a;
            --bg-tertiary: #333333;
            --bg-hover: #3a3a3a;
            --text-primary: #f5f5f5;
            --text-secondary: #a0a0a0;
            --text-muted: #666666;
            --accent: #d97706;
            --accent-hover: #f59e0b;
            --user-bg: #3b3b3b;
            --assistant-bg: transparent;
            --border: #404040;
            --border-light: #4a4a4a;
            --success: #22c55e;
            --error: #ef4444;
        }
        
        html, body {
            height: 100%;
            overflow: hidden;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            display: flex;
            flex-direction: column;
        }
        
        /* Header */
        .header {
            padding: 16px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: var(--bg-secondary);
        }
        
        .logo {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .logo-icon {
            width: 32px;
            height: 32px;
            background: linear-gradient(135deg, var(--accent), #ea580c);
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 18px;
        }
        
        .logo-text {
            font-size: 20px;
            font-weight: 600;
            letter-spacing: -0.02em;
        }
        
        .model-badge {
            background: var(--bg-tertiary);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12px;
            color: var(--text-secondary);
            border: 1px solid var(--border);
        }
        
        /* Chat area */
        .chat-wrapper {
            flex: 1;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        
        .chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 24px 0;
        }
        
        .chat-content {
            max-width: 800px;
            margin: 0 auto;
            padding: 0 24px;
        }
        
        .message {
            margin-bottom: 24px;
            padding: 16px 0;
        }
        
        .message-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 12px;
        }
        
        .avatar {
            width: 28px;
            height: 28px;
            border-radius: 6px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 600;
        }
        
        .user-avatar {
            background: var(--accent);
            color: white;
        }
        
        .assistant-avatar {
            background: linear-gradient(135deg, #8b5cf6, #6366f1);
            color: white;
        }
        
        .message-name {
            font-weight: 500;
            font-size: 14px;
        }
        
        .message-content {
            font-size: 15px;
            line-height: 1.7;
            color: var(--text-primary);
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        
        .message-content p {
            margin-bottom: 12px;
        }
        
        .message-content p:last-child {
            margin-bottom: 0;
        }
        
        /* Typing cursor */
        .cursor {
            display: inline-block;
            width: 2px;
            height: 18px;
            background: var(--accent);
            margin-left: 2px;
            animation: blink 1s step-end infinite;
            vertical-align: text-bottom;
        }
        
        @keyframes blink {
            0%, 100% { opacity: 1; }
            50% { opacity: 0; }
        }
        
        /* Metrics bar */
        .metrics-bar {
            display: flex;
            gap: 16px;
            margin-top: 12px;
            padding-top: 12px;
            border-top: 1px solid var(--border);
            font-size: 12px;
            color: var(--text-muted);
        }
        
        .metric {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        
        .metric-value {
            color: var(--text-secondary);
            font-weight: 500;
        }
        
        /* Input area */
        .input-wrapper {
            border-top: 1px solid var(--border);
            background: var(--bg-primary);
            padding: 16px 24px 24px;
        }
        
        .input-container {
            max-width: 800px;
            margin: 0 auto;
            position: relative;
        }
        
        .input-box {
            display: flex;
            align-items: flex-end;
            background: var(--bg-secondary);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 12px 16px;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        
        .input-box:focus-within {
            border-color: var(--accent);
            box-shadow: 0 0 0 1px var(--accent);
        }
        
        textarea {
            flex: 1;
            background: transparent;
            border: none;
            color: var(--text-primary);
            font-family: inherit;
            font-size: 15px;
            line-height: 1.5;
            resize: none;
            max-height: 200px;
            outline: none;
        }
        
        textarea::placeholder {
            color: var(--text-muted);
        }
        
        .send-btn {
            width: 36px;
            height: 36px;
            background: var(--accent);
            border: none;
            border-radius: 10px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.2s, transform 0.1s;
            margin-left: 12px;
            flex-shrink: 0;
        }
        
        .send-btn:hover:not(:disabled) {
            background: var(--accent-hover);
            transform: scale(1.05);
        }
        
        .send-btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        .send-btn svg {
            width: 18px;
            height: 18px;
            fill: white;
        }
        
        /* Status */
        .status-bar {
            text-align: center;
            padding: 8px;
            font-size: 12px;
            color: var(--text-muted);
        }
        
        .status-bar.connected {
            color: var(--success);
        }
        
        .status-bar.error {
            color: var(--error);
        }
        
        /* Empty state */
        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: var(--text-muted);
            text-align: center;
            padding: 48px 24px;
        }
        
        .empty-icon {
            width: 64px;
            height: 64px;
            background: linear-gradient(135deg, var(--accent), #ea580c);
            border-radius: 16px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 32px;
            margin-bottom: 24px;
        }
        
        .empty-title {
            font-size: 24px;
            font-weight: 600;
            color: var(--text-primary);
            margin-bottom: 8px;
        }
        
        .empty-subtitle {
            font-size: 15px;
            max-width: 400px;
        }
        
        /* Scrollbar */
        ::-webkit-scrollbar {
            width: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: transparent;
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--border);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--border-light);
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">
            <div class="logo-icon">⚡</div>
            <span class="logo-text">Flux</span>
        </div>
        <div class="model-badge">Qwen3-1.7B • Streaming</div>
    </div>
    
    <div class="chat-wrapper">
        <div class="chat-container" id="chatContainer">
            <div class="chat-content" id="chatContent">
                <div class="empty-state" id="emptyState">
                    <div class="empty-icon">⚡</div>
                    <h2 class="empty-title">How can I help you today?</h2>
                    <p class="empty-subtitle">Send a message to start a conversation with Flux AI</p>
                </div>
            </div>
        </div>
        
        <div class="input-wrapper">
            <div class="input-container">
                <div class="input-box">
                    <textarea 
                        id="promptInput" 
                        placeholder="Message Flux..." 
                        rows="1"
                        onkeydown="handleKeyDown(event)"
                        oninput="autoResize(this)"
                    ></textarea>
                    <button class="send-btn" id="sendBtn" onclick="sendMessage()">
                        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
                            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
                        </svg>
                    </button>
                </div>
                <div class="status-bar" id="status">Ready</div>
            </div>
        </div>
    </div>

    <script>
        let ws = null;
        let currentMessageEl = null;
        let startTime = null;
        let firstTokenTime = null;
        let tokenCount = 0;
        
        const chatContent = document.getElementById('chatContent');
        const chatContainer = document.getElementById('chatContainer');
        const promptInput = document.getElementById('promptInput');
        const sendBtn = document.getElementById('sendBtn');
        const status = document.getElementById('status');
        const emptyState = document.getElementById('emptyState');
        
        function autoResize(el) {
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 200) + 'px';
        }
        
        function handleKeyDown(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        }
        
        function updateStatus(text, className = '') {
            status.textContent = text;
            status.className = 'status-bar ' + className;
        }
        
        function hideEmptyState() {
            if (emptyState) {
                emptyState.style.display = 'none';
            }
        }
        
        function addUserMessage(text) {
            hideEmptyState();
            const div = document.createElement('div');
            div.className = 'message';
            div.innerHTML = `
                <div class="message-header">
                    <div class="avatar user-avatar">U</div>
                    <span class="message-name">You</span>
                </div>
                <div class="message-content">${escapeHtml(text)}</div>
            `;
            chatContent.appendChild(div);
            scrollToBottom();
        }
        
        function startAssistantMessage() {
            const div = document.createElement('div');
            div.className = 'message';
            div.innerHTML = `
                <div class="message-header">
                    <div class="avatar assistant-avatar">F</div>
                    <span class="message-name">Flux</span>
                </div>
                <div class="message-content"><span class="cursor"></span></div>
            `;
            chatContent.appendChild(div);
            currentMessageEl = div.querySelector('.message-content');
            scrollToBottom();
        }
        
        function appendToken(token) {
            if (!currentMessageEl) return;
            
            // Record first token time
            if (!firstTokenTime) {
                firstTokenTime = Date.now();
            }
            tokenCount++;
            
            // Remove cursor, add token, add cursor back
            const cursor = currentMessageEl.querySelector('.cursor');
            if (cursor) cursor.remove();
            
            // Append token
            currentMessageEl.innerHTML += escapeHtml(token);
            currentMessageEl.innerHTML += '<span class="cursor"></span>';
            
            scrollToBottom();
        }
        
        function endAssistantMessage(metrics = null) {
            if (!currentMessageEl) return;
            
            const cursor = currentMessageEl.querySelector('.cursor');
            if (cursor) cursor.remove();
            
            // Add metrics bar if we have metrics
            if (metrics || tokenCount > 0) {
                const endTime = Date.now();
                const totalTime = (endTime - startTime) / 1000;
                const ttft = firstTokenTime ? ((firstTokenTime - startTime) / 1000) : 0;
                const genTime = firstTokenTime ? ((endTime - firstTokenTime) / 1000) : totalTime;
                const tps = genTime > 0 ? (tokenCount / genTime) : 0;
                
                // Use server metrics if available
                const displayMetrics = metrics || {
                    ttft_ms: ttft * 1000,
                    total_time_ms: totalTime * 1000,
                    tokens_per_second: tps
                };
                
                const metricsHtml = `
                    <div class="metrics-bar">
                        <div class="metric">
                            <span>Tokens:</span>
                            <span class="metric-value">${metrics?.token_count || tokenCount}</span>
                        </div>
                        <div class="metric">
                            <span>TTFT:</span>
                            <span class="metric-value">${displayMetrics.ttft_ms?.toFixed(0) || '—'}ms</span>
                        </div>
                        <div class="metric">
                            <span>Speed:</span>
                            <span class="metric-value">${displayMetrics.tokens_per_second?.toFixed(1) || '—'} tok/s</span>
                        </div>
                        <div class="metric">
                            <span>Total:</span>
                            <span class="metric-value">${(displayMetrics.total_time_ms / 1000)?.toFixed(2) || '—'}s</span>
                        </div>
                        ${displayMetrics.llama_generation_tps ? `
                        <div class="metric">
                            <span>llama.cpp:</span>
                            <span class="metric-value">${displayMetrics.llama_generation_tps} tok/s</span>
                        </div>
                        ` : ''}
                    </div>
                `;
                
                currentMessageEl.parentElement.innerHTML += metricsHtml;
            }
            
            currentMessageEl = null;
            tokenCount = 0;
            firstTokenTime = null;
        }
        
        function scrollToBottom() {
            chatContainer.scrollTop = chatContainer.scrollHeight;
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
            promptInput.style.height = 'auto';
            
            startTime = Date.now();
            firstTokenTime = null;
            tokenCount = 0;
            
            // Connect WebSocket
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${window.location.host}/ws/generate`);
            
            ws.onopen = () => {
                updateStatus('Connected • Generating...', 'connected');
                startAssistantMessage();
                ws.send(JSON.stringify({
                    prompt: prompt,
                    max_tokens: 2048
                }));
            };
            
            ws.onmessage = (event) => {
                const data = JSON.parse(event.data);
                
                if (data.type === 'token') {
                    appendToken(data.content);
                } else if (data.type === 'complete') {
                    endAssistantMessage(data.metrics);
                    const tps = data.metrics?.tokens_per_second || '—';
                    updateStatus(`Completed • ${data.token_count} tokens • ${tps} tok/s`, 'connected');
                } else if (data.type === 'error') {
                    endAssistantMessage();
                    updateStatus(`Error: ${data.error}`, 'error');
                }
            };
            
            ws.onclose = () => {
                sendBtn.disabled = false;
                promptInput.disabled = false;
                promptInput.focus();
                if (status.textContent.includes('Generating')) {
                    updateStatus('Disconnected');
                }
            };
            
            ws.onerror = () => {
                updateStatus('Connection error', 'error');
                sendBtn.disabled = false;
                promptInput.disabled = false;
            };
        }
        
        // Focus input on load
        promptInput.focus();
    </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def home():
    """Serve the modern chat UI"""
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
    
    THIS IS REAL STREAMING - NOT MOCKED!
    =====================================
    Flow:
    1. Accept WebSocket connection from client
    2. Receive generation request (prompt, max_tokens)
    3. Subscribe to Redis Pub/Sub channel for this request
    4. Push job to Redis queue (worker will pop it)
    5. Worker runs llama.cpp and publishes EACH TOKEN to Redis Pub/Sub
    6. We receive each token from Redis Pub/Sub in real-time
    7. We forward each token to client via WebSocket immediately
    
    Redis is used both for:
    - Queue (job distribution): Gateway RPUSH -> Worker BLPOP
    - Pub/Sub (token streaming): Worker PUBLISH -> Gateway SUBSCRIBE
    """
    await websocket.accept()
    ACTIVE_CONNECTIONS.inc()
    
    request_id = str(uuid.uuid4())
    redis_client = get_redis()
    pubsub = redis_client.pubsub()
    start_time = time.time()
    first_token_time = None
    token_count = 0
    
    try:
        # Receive the generation request
        data = await websocket.receive_text()
        request = json.loads(data)
        
        prompt = request.get("prompt", "")
        max_tokens = request.get("max_tokens", DEFAULT_MAX_TOKENS)
        
        if not prompt:
            await websocket.send_json({"type": "error", "error": "Prompt is required"})
            REQUEST_COUNT.labels(status="error").inc()
            return
        
        # Create job payload - no temperature needed (always deterministic)
        job = {
            "request_id": request_id,
            "prompt": prompt,
            "max_tokens": max_tokens,
        }
        
        # Subscribe to the response channel BEFORE pushing the job
        # This ensures we don't miss any tokens
        channel = f"{CHANNEL_PREFIX}:{request_id}"
        await pubsub.subscribe(channel)
        
        # Push job to the queue - worker will pop it
        await redis_client.rpush(QUEUE_NAME, json.dumps(job))
        
        # Stream responses from the worker via Redis Pub/Sub
        # Each message contains a single token - TRUE streaming!
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                
                # Forward token to WebSocket immediately
                await websocket.send_json(data)
                
                # Track metrics
                if data.get("type") == "token":
                    if first_token_time is None:
                        first_token_time = time.time()
                        TTFT.observe(first_token_time - start_time)
                    token_count += 1
                
                # End stream on complete or error
                if data.get("type") == "complete":
                    REQUEST_COUNT.labels(status="success").inc()
                    TOKENS_GENERATED.inc(data.get("token_count", token_count))
                    
                    total_time = time.time() - start_time
                    REQUEST_LATENCY.observe(total_time)
                    
                    if data.get("metrics", {}).get("tokens_per_second"):
                        TOKENS_PER_SECOND.observe(data["metrics"]["tokens_per_second"])
                    
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
        # Cleanup Redis subscription
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
