# Flux Makefile
# Common operations for development and deployment

.PHONY: help setup download-model dev docker-build docker-up docker-down k8s-setup k8s-deploy k8s-clean test lint

# Default target
help:
	@echo "╔══════════════════════════════════════════════════════════════╗"
	@echo "║            ⚡ Flux: LLM Orchestration                  ║"
	@echo "╚══════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "📦 Setup Commands:"
	@echo "  make setup            - Set up Python environment"
	@echo "  make download-model   - Download the LLM model (~1.8GB)"
	@echo ""
	@echo "🐳 Docker Commands:"
	@echo "  make docker-build     - Build Docker images"
	@echo "  make docker-up        - Start services with docker-compose"
	@echo "  make docker-down      - Stop all services"
	@echo "  make docker-logs      - View service logs"
	@echo ""
	@echo "☸️  Kubernetes Commands:"
	@echo "  make k8s-setup        - Set up Minikube and dependencies"
	@echo "  make k8s-build        - Build images in Minikube"
	@echo "  make k8s-deploy       - Deploy to Kubernetes"
	@echo "  make k8s-observability- Deploy Prometheus & Grafana"
	@echo "  make k8s-clean        - Remove Kubernetes resources"
	@echo "  make k8s-status       - Check cluster status"
	@echo ""
	@echo "📊 Observability:"
	@echo "  make grafana-port     - Port-forward Grafana (localhost:3000)"
	@echo "  make prometheus-port  - Port-forward Prometheus (localhost:9090)"
	@echo ""
	@echo "🧪 Testing Commands:"
	@echo "  make load-test        - Run load test (10 requests)"
	@echo "  make flood-queue      - Flood queue with 50 requests (KEDA demo)"
	@echo "  make test             - Run unit tests"

# ═══════════════════════════════════════════════════════════════
# Setup
# ═══════════════════════════════════════════════════════════════

setup:
	@echo "📦 Setting up Python environment..."
	uv sync
	@echo "✅ Environment ready! Run 'source .venv/bin/activate'"

download-model:
	@echo "📦 Downloading LLM model..."
	@chmod +x scripts/download_model.sh
	@./scripts/download_model.sh

# ═══════════════════════════════════════════════════════════════
# Docker
# ═══════════════════════════════════════════════════════════════

docker-build:
	@echo "🐳 Building Docker images..."
	docker-compose build

docker-up: docker-build
	@echo "🚀 Starting services..."
	docker-compose up -d
	@echo ""
	@echo "✅ Services starting!"
	@echo "   Gateway: http://localhost:8000"
	@echo "   Metrics: http://localhost:8000/metrics"
	@echo ""
	@echo "⏳ Note: Worker needs to build llama.cpp (~2-5 min first time)"
	@echo "   Watch logs: make docker-logs"

docker-down:
	@echo "🛑 Stopping services..."
	docker-compose down

docker-logs:
	docker-compose logs -f

docker-logs-worker:
	docker-compose logs -f worker

docker-logs-gateway:
	docker-compose logs -f gateway

docker-clean:
	docker-compose down -v --rmi local

docker-restart:
	docker-compose restart

# ═══════════════════════════════════════════════════════════════
# Kubernetes
# ═══════════════════════════════════════════════════════════════

k8s-setup:
	@echo "☸️  Setting up Minikube..."
	minikube start --cpus 4 --memory 8192
	@echo "📦 Installing NGINX Ingress..."
	minikube addons enable ingress
	@echo "📦 Installing KEDA..."
	helm repo add kedacore https://kedacore.github.io/charts
	helm repo update
	helm install keda kedacore/keda --namespace keda --create-namespace
	@echo "✅ Minikube ready!"

k8s-build:
	@echo "🐳 Building images in Minikube..."
	@echo "⏳ This will take a while (building llama.cpp from source)..."
	eval $$(minikube docker-env) && \
	docker build -t llm-gateway:v1 ./services/gateway && \
	docker build -t llm-worker:v1 ./services/worker
	@echo "✅ Images built!"

k8s-deploy:
	@echo "☸️  Deploying to Kubernetes..."
	kubectl apply -f k8s/namespace.yaml
	helm install redis oci://registry-1.docker.io/bitnami/charts/redis \
		--namespace Flux \
		--set auth.enabled=false \
		--set architecture=standalone \
		--wait || true
	kubectl apply -f k8s/configmap.yaml
	kubectl apply -f k8s/gateway-deployment.yaml
	kubectl apply -f k8s/worker-deployment.yaml
	kubectl apply -f k8s/ingress.yaml
	kubectl apply -f k8s/keda-scaledobject.yaml
	@echo ""
	@echo "✅ Deployed!"
	@echo ""
	@echo "📝 Next steps:"
	@echo "   1. Add to /etc/hosts: 127.0.0.1 api.local"
	@echo "   2. Run: minikube tunnel"
	@echo "   3. Open: http://api.local"

k8s-observability:
	@echo "📊 Deploying observability stack..."
	kubectl apply -f k8s/observability/prometheus-config.yaml
	kubectl apply -f k8s/observability/prometheus-deployment.yaml
	kubectl apply -f k8s/observability/grafana-config.yaml
	kubectl apply -f k8s/observability/grafana-deployment.yaml
	@echo ""
	@echo "✅ Observability deployed!"
	@echo "   Grafana: make grafana-port (admin/asyncscale)"
	@echo "   Prometheus: make prometheus-port"

k8s-helm-deploy:
	@echo "☸️  Deploying with Helm..."
	kubectl create namespace Flux --dry-run=client -o yaml | kubectl apply -f -
	helm install Flux ./k8s/charts/llm-stack --namespace Flux
	@echo "✅ Deployed with Helm!"

k8s-clean:
	@echo "🧹 Cleaning Kubernetes resources..."
	-kubectl delete -f k8s/keda-scaledobject.yaml
	-kubectl delete -f k8s/ingress.yaml
	-kubectl delete -f k8s/worker-deployment.yaml
	-kubectl delete -f k8s/gateway-deployment.yaml
	-kubectl delete -f k8s/configmap.yaml
	-kubectl delete -f k8s/observability/
	-helm uninstall redis --namespace Flux
	-kubectl delete namespace Flux

k8s-status:
	@echo "📊 Cluster Status:"
	@echo ""
	@echo "Pods:"
	@kubectl get pods -n Flux
	@echo ""
	@echo "Services:"
	@kubectl get svc -n Flux
	@echo ""
	@echo "KEDA ScaledObject:"
	@kubectl get scaledobject -n Flux 2>/dev/null || echo "  Not found"
	@echo ""
	@echo "HPA (managed by KEDA):"
	@kubectl get hpa -n Flux 2>/dev/null || echo "  Not found"

# ═══════════════════════════════════════════════════════════════
# Observability Port Forwarding
# ═══════════════════════════════════════════════════════════════

grafana-port:
	@echo "📊 Opening Grafana at http://localhost:3000"
	@echo "   Login: admin / asyncscale"
	kubectl port-forward -n Flux svc/grafana 3000:3000

prometheus-port:
	@echo "📊 Opening Prometheus at http://localhost:9090"
	kubectl port-forward -n Flux svc/prometheus 9090:9090

# ═══════════════════════════════════════════════════════════════
# Development
# ═══════════════════════════════════════════════════════════════

dev:
	@echo "🔧 Starting local development..."
	@echo ""
	@echo "1. Start Redis:"
	@echo "   make redis-start"
	@echo ""
	@echo "2. Start Gateway (Terminal 1):"
	@echo "   source .venv/bin/activate"
	@echo "   cd services/gateway && python gateway.py"
	@echo ""
	@echo "3. Start Worker (Terminal 2) - requires llama.cpp built locally:"
	@echo "   source .venv/bin/activate"
	@echo "   cd services/worker && python worker.py"
	@echo ""
	@echo "💡 For local worker, you need llama.cpp built. Use Docker instead:"
	@echo "   make docker-up"

dev-gateway:
	cd services/gateway && python gateway.py

redis-start:
	@echo "🔗 Starting Redis..."
	docker run -d --name redis-dev -p 6379:6379 redis:alpine
	@echo "✅ Redis running on localhost:6379"

redis-stop:
	docker stop redis-dev && docker rm redis-dev

redis-cli:
	docker exec -it redis-dev redis-cli

# ═══════════════════════════════════════════════════════════════
# Load Testing
# ═══════════════════════════════════════════════════════════════

load-test:
	@echo "🧪 Running load test..."
	python scripts/load_test.py --url http://localhost:8000 -n 10 -c 3

load-test-heavy:
	@echo "🧪 Running heavy load test..."
	python scripts/load_test.py --url http://localhost:8000 -n 50 -c 10

flood-queue:
	@echo "🌊 Flooding queue for KEDA autoscaling demo..."
	python scripts/load_test.py --url http://localhost:8000 -n 50 --flood

queue-status:
	@curl -s http://localhost:8000/queue/status | python -m json.tool

metrics:
	@curl -s http://localhost:8000/metrics

# ═══════════════════════════════════════════════════════════════
# Testing & Linting
# ═══════════════════════════════════════════════════════════════

test:
	@echo "🧪 Running tests..."
	uv run pytest -v

lint:
	@echo "🔍 Running linter..."
	uv run ruff check .

format:
	@echo "✨ Formatting code..."
	uv run ruff format .
