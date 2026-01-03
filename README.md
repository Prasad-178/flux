# ⚡ Flux: Event-Driven LLM Orchestration

> High-performance, autoscaling inference architecture using Kubernetes, KEDA, and WebSockets for real-time token streaming.

![Architecture](https://img.shields.io/badge/Architecture-Event--Driven-blue)
![Python](https://img.shields.io/badge/Python-3.11-green)
![Kubernetes](https://img.shields.io/badge/Platform-Kubernetes-326CE5)
![llama.cpp](https://img.shields.io/badge/Inference-llama.cpp-orange)

## 🎯 Overview

A production-grade **LLMOps architecture** that handles high-concurrency inference requests without dropping connections. Uses an **Event-Driven Architecture (EDA)** with:

- **Redis Queue** for decoupling API from inference workers
- **WebSockets** for real-time token streaming (ChatGPT-style)
- **KEDA** for intelligent autoscaling based on queue depth
- **Native llama.cpp** for high-performance CPU inference
- **Prometheus/Grafana** for observability

## 🏗️ Architecture

```
┌─────────────┐      WebSocket       ┌─────────────┐
│   Client    │◄────────────────────►│   Gateway   │──► /metrics
└─────────────┘                      └──────┬──────┘
                                            │
                                   rpush    │    subscribe
                                            ▼
                                     ┌──────────────┐
                                     │    Redis     │
                                     │  Queue/PubSub│
                                     └──────┬───────┘
                                            │
                                   blpop    │    publish
                                            ▼
                                     ┌──────────────┐
                                     │   Worker     │ ◄── KEDA scales based
                                     │  (llama.cpp) │     on queue depth
                                     └──────────────┘
```

### Workflow

1. **Client** connects via WebSocket to `/ws/generate`
2. **Gateway** creates request ID, pushes job to Redis queue, subscribes to response channel
3. **KEDA** monitors queue depth, scales workers when jobs > threshold
4. **Worker** pops job, runs llama.cpp CLI, streams tokens via Redis Pub/Sub
5. **Gateway** receives tokens from Redis, forwards to client in real-time

## 📦 Tech Stack

| Component | Technology |
|-----------|------------|
| Orchestration | Kubernetes (Minikube) |
| Traffic | NGINX Ingress |
| Message Broker | Redis (Queue + Pub/Sub) |
| Autoscaling | KEDA |
| API Gateway | FastAPI + WebSockets |
| Inference | **llama.cpp** (native CLI, built from source) |
| Observability | Prometheus + Grafana |
| Model | Qwen 3 1.7B (Q8_0 quantized) |

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.11+
- ~2GB disk space for model
- For K8s: Minikube, Helm, kubectl

### Option 1: Docker Compose (Recommended for Testing)

```bash
# 1. Download the model (~1.8GB)
make download-model

# 2. Build and start (first build takes ~5 min for llama.cpp)
make docker-up

# 3. Watch logs
make docker-logs

# 4. Open http://localhost:8000
```

### Option 2: Kubernetes (Full Production Setup)

```bash
# 1. Setup Minikube with KEDA
make k8s-setup

# 2. Build images in Minikube
make k8s-build

# 3. Deploy everything
make k8s-deploy

# 4. Deploy observability
make k8s-observability

# 5. Add to /etc/hosts and tunnel
echo "127.0.0.1 api.local" | sudo tee -a /etc/hosts
minikube tunnel

# 6. Open http://api.local
```

## 🔌 API Endpoints

### WebSocket: `ws://localhost:8000/ws/generate`

Real-time streaming generation.

```json
// Request
{"prompt": "What is AI?", "max_tokens": 512, "temperature": 0.7}

// Response stream
{"type": "start", "request_id": "uuid"}
{"type": "token", "content": "A", "request_id": "uuid"}
{"type": "token", "content": "rtificial", "request_id": "uuid"}
{"type": "complete", "request_id": "uuid", "token_count": 142}
```

### REST Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Chat UI |
| `GET /health` | Health check |
| `GET /metrics` | Prometheus metrics |
| `GET /queue/status` | Queue depth |
| `POST /api/generate` | Non-streaming (queues job) |

## 📊 Observability

### Prometheus Metrics

- `gateway_requests_total` - Request count by status
- `gateway_active_websocket_connections` - Active connections
- `gateway_redis_queue_length` - Queue depth
- `gateway_request_latency_seconds` - Request latency histogram
- `gateway_tokens_generated_total` - Total tokens generated

### Grafana Dashboard

Access Grafana after deploying observability:

```bash
make grafana-port  # Opens localhost:3000
# Login: admin / asyncscale
```

Dashboard includes:
- Queue length gauge
- Active WebSocket connections
- Worker pod count
- Request rate over time
- Latency percentiles (p50, p95, p99)
- Token generation rate

## 🧪 Load Testing & KEDA Demo

### Test with Load

```bash
# Light test (10 requests)
make load-test

# Heavy test (50 requests)
make load-test-heavy

# Watch KEDA scale workers
kubectl get pods -n Flux -w
```

### KEDA Autoscaling Demo

```bash
# Flood the queue with 50 jobs
make flood-queue

# Watch KEDA scale workers from 1 → N
kubectl get pods -n Flux -w

# Check queue status
make queue-status
```

## 📁 Project Structure

```
Flux/
├── services/
│   ├── gateway/              # FastAPI WebSocket Gateway
│   │   ├── gateway.py        # WebSocket handling, Prometheus metrics
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   └── worker/               # Inference Worker
│       ├── worker.py         # Redis consumer, llama.cpp CLI wrapper
│       ├── Dockerfile        # Builds llama.cpp from source
│       └── requirements.txt
├── k8s/
│   ├── namespace.yaml
│   ├── configmap.yaml
│   ├── gateway-deployment.yaml
│   ├── worker-deployment.yaml
│   ├── ingress.yaml
│   ├── keda-scaledobject.yaml
│   ├── observability/        # Prometheus & Grafana
│   └── charts/llm-stack/     # Helm Chart
├── scripts/
│   ├── download_model.sh
│   └── load_test.py
├── models/                   # Model files (gitignored)
├── docker-compose.yml
├── Makefile
└── pyproject.toml
```

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_HOST` | localhost | Redis host |
| `REDIS_PORT` | 6379 | Redis port |
| `QUEUE_NAME` | llm_queue | Job queue name |
| `CHANNEL_PREFIX` | task_channel | Pub/Sub channel prefix |
| `MODEL_PATH` | /models/Qwen3-1.7B-Q8_0.gguf | Model file path |
| `LLAMA_CLI_PATH` | /opt/llama.cpp/build/bin/llama-cli | llama-cli binary |
| `N_CTX` | 2048 | Context window |
| `N_THREADS` | 4 | CPU threads |
| `N_GPU_LAYERS` | 0 | GPU layers (0=CPU only) |

## 🛤️ Roadmap

- [x] Phase 1: Local Docker Prototype
- [x] Phase 2: Kubernetes Foundations
- [x] Phase 3: Containerization & Deployment
- [x] Phase 4: KEDA Autoscaling
- [x] Phase 5: Observability (Prometheus/Grafana)

## 🧠 Key Learnings

- **Event-Driven Architecture**: Decoupling request handling from compute
- **Streaming with Pub/Sub**: Real-time token delivery via Redis channels
- **KEDA Operators**: Extending K8s with queue-based autoscaling
- **Native llama.cpp**: Building from source for best performance
- **LLMOps**: Serving quantized models efficiently on CPU

## 📄 License

MIT License
