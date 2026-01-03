# 🚀 Flux: Event-Driven LLM Orchestration on Kubernetes

> A high-performance, autoscaling inference architecture using Kubernetes, KEDA, and WebSockets for real-time token streaming.

## 📋 Project Overview
This project demonstrates a production-grade **LLMOps architecture** designed to handle high-concurrency inference requests without dropping connections or crashing servers. 

Unlike simple REST API wrappers, this system uses an **Event-Driven Architecture (EDA)**. It decouples the API handling from the heavy GPU/CPU inference processing using a **Redis Queue**. It leverages **WebSockets** for real-time token streaming (similar to ChatGPT) and utilizes **KEDA** to autoscale inference workers based on queue depth, ensuring resource efficiency.

### 🎯 Key Objectives
1.  **Asynchronous Processing:** Move blocking inference tasks off the main web server.
2.  **Real-Time Streaming:** Stream tokens from the worker -> Redis -> API -> Client via WebSockets.
3.  **Intelligent Autoscaling:** Use **KEDA** to scale workers based on *pending jobs*, not just CPU usage.
4.  **Infrastructure as Code:** Fully deployed via **Helm Charts** on **Minikube**.

---

## 🏗️ Architecture & Tech Stack



### The Stack
* **Orchestration:** [Kubernetes (Minikube)](https://minikube.sigs.k8s.io/)
* **Traffic Management:** [NGINX Ingress Controller](https://kubernetes.github.io/ingress-nginx/)
* **Message Broker:** [Redis](https://redis.io/) (Used for Job Queue & Pub/Sub Streaming)
* **Autoscaling:** [KEDA](https://keda.sh/) (Event-driven scaling)
* **API Gateway:** [FastAPI](https://fastapi.tiangolo.com/) (Async Python, WebSockets)
* **Inference Engine:** [llama.cpp](https://github.com/ggerganov/llama.cpp) (Python bindings)
* **Observability:** [Prometheus](https://prometheus.io/) & [Grafana](https://grafana.com/)
* **Model:** Qwen 3 1.7B (Quantized: `q8_0`) - *Optimized for CPU inference.*

### The Workflow
1.  **Client Connection:** User connects to `wss://api.local/generate` via WebSocket.
2.  **Job Dispatch:** The **API Gateway** creates a generic `RequestID`, pushes the prompt to the **Redis List** (`llm_queue`), and subscribes to a **Redis Channel** (`task_channel:<RequestID>`).
3.  **Worker Scaling:** **KEDA** detects items in `llm_queue`. If queue > 0, it scales up **Inference Pods**.
4.  **Inference:** An idle **Inference Pod** pops the job. It loads the model and generates tokens.
5.  **Streaming:** As each token is generated, the Worker publishes it to `task_channel:<RequestID>`.
6.  **Response:** The **API Gateway** receives the stream from Redis and forwards it down the WebSocket to the user in real-time.

---

## 📦 Component Breakdown

### 1. The API Gateway (`/services/gateway`)
* **Role:** The "Front of House". Lightweight, handles connection management.
* **Tech:** Python 3.11, FastAPI, `redis-py`.
* **Key Logic:**
    * Accepts WS connection.
    * `lpush` to Redis Queue.
    * `subscribe` to Redis Pub/Sub.
    * Loop: Receive message -> `ws.send_text()`.

### 2. The Inference Worker (`/services/worker`)
* **Role:** The "Back of House" muscle. Heavy compute.
* **Tech:** Python 3.11, `llama-cpp-python`.
* **Model:** `Qwen/Qwen3-1.7B-GGUF` (q8_0). (unsloth model download link - https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q8_0.gguf?download=true)
* **Key Logic:**
    * Infinite Loop: `blpop` (Blocking Pop) from Redis Queue.
    * Run `llm.create_chat_completion(stream=True)`.
    * For chunk in stream: `redis.publish(chunk)`.

### 3. The Infrastructure (`/k8s`)
* **Helm Charts:**
    * `redis-ha`: High availability Redis.
    * `ingress-nginx`: Load balancing.
    * `prometheus-stack`: Monitoring.
    * `llm-stack` (Custom Chart): Deploys our Gateway and Worker with KEDA configurations.

---

## 🛤️ Implementation Roadmap

### Phase 1: Local Docker Prototype
* [ ] Write `worker.py` to consume from Redis and run `llama.cpp`.
* [ ] Write `gateway.py` to handle WebSockets and Redis Pub/Sub.
* [ ] Create `docker-compose.yml` to spin up Redis, Gateway, and Worker locally.
* [ ] **Goal:** Verify streaming works on `localhost` without K8s.

### Phase 2: Kubernetes Foundations
* [ ] Start Minikube: `minikube start --cpus 4 --memory 8192`.
* [ ] Install Redis via Helm.
* [ ] Install NGINX Ingress.
* [ ] **Goal:** Have a running cluster with a database.

### Phase 3: Containerization & Deployment
* [ ] Build Docker images for Gateway and Worker.
* [ ] Write Kubernetes Manifests (Deployment, Service, ConfigMap).
* [ ] Deploy Gateway and verify WebSocket connection via `wscat` or Postman.
* [ ] Deploy Worker and verify it picks up jobs.

### Phase 4: Autoscaling (The "Magic")
* [ ] Install KEDA via Helm.
* [ ] Create `ScaledObject` resource targeting the Worker Deployment.
* [ ] **Test:** Flood the queue with 50 requests. Watch KEDA scale workers from 1 -> 5.

### Phase 5: Observability
* [ ] Install Prometheus/Grafana.
* [ ] Create a Dashboard showing:
    * `redis_queue_length`
    * `active_inference_pods`
    * `inference_latency`

---

## 🔧 Setup Instructions (Quick Start)

*(To be filled as we build)*

1.  **Prerequisites:** Docker, Minikube, Helm, Python 3.11.
2.  **Build Images:**
    ```bash
    eval $(minikube docker-env) # Point shell to Minikube's Docker daemon
    docker build -t llm-worker:v1 ./services/worker
    docker build -t llm-gateway:v1 ./services/gateway
    ```
3.  **Deploy Stack:**
    ```bash
    helm install redis oci://registry-1.docker.io/bitnami/charts/redis
    kubectl apply -f k8s/
    ```

---

## 🧠 What I Learned
* **Async Patterns:** How to decouple HTTP request lifecycles from heavy compute tasks.
* **Kubernetes Operators:** Using KEDA to extend K8s capabilities.
* **Streaming Architectures:** Managing state across distributed systems using Pub/Sub.
* **LLMOps:** Serving quantized models efficiently on CPU-constrained environments.