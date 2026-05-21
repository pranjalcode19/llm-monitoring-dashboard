# llm-monitoring-dashboard

Real-time observability dashboard for LLM applications. Tracks request latency, error rate, and success rate — stores history in SQLite and exposes Prometheus metrics for Grafana dashboards.

## What it does

| Endpoint | Description |
|---|---|
| `POST /ask` | Send a question to the LLM, records latency + status to SQLite and Prometheus |
| `GET /` | Live dashboard (HTML) — latency chart, success rate, recent requests |
| `GET /metrics` | Prometheus scrape endpoint (`llm_requests_total`, `llm_request_latency_seconds`) |
| `GET /metrics/summary` | JSON summary — total requests, avg latency, error count, success rate |
| `GET /metrics/raw` | Last 100 requests from SQLite |
| `GET /health` | Health check for K8s probes |

## Prometheus metrics

| Metric | Type | Description |
|---|---|---|
| `llm_requests_total{status}` | Counter | Total requests split by success/error |
| `llm_request_latency_seconds` | Histogram | Latency distribution (p50, p95, p99) |
| `llm_errors_total` | Counter | Total error count |

**Useful PromQL queries:**
```promql
# Requests per second
rate(llm_requests_total[5m])

# p95 latency
histogram_quantile(0.95, rate(llm_request_latency_seconds_bucket[5m]))

# Error rate
rate(llm_errors_total[5m]) / rate(llm_requests_total[5m])
```

## Run locally

**Prerequisites:** [Ollama](https://ollama.com) running with `llama3.2` pulled.

```bash
pip install -r requirements.txt
uvicorn server:app --reload --port 8000
```

Open http://localhost:8000 for the dashboard.

## Run with Docker

```bash
docker build -t llm-monitoring-dashboard .
docker run -p 8000:8000 \
  -e OLLAMA_HOST=http://host.docker.internal:11434 \
  -v llm-metrics:/app/data \
  -e DB_PATH=/app/data/metrics.db \
  llm-monitoring-dashboard
```

## Deploy to Kubernetes

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/

# Or via Helm
helm upgrade --install llm-monitoring-dashboard \
  ../devops-ai-assistant/helm \
  -f helm-values/llm-monitoring-dashboard.yaml \
  -n ai-platform --create-namespace \
  --set image.tag=<git-sha>
```

## K8s architecture

```
Ingress (/monitor)
  └── Service (ClusterIP :80)
        └── Deployment
              └── Container: uvicorn server:app
                    └── PVC: /app/data (metrics.db persisted across restarts)
```

**Key design decisions:**
- `DB_PATH` env var — defaults to `metrics.db` locally, set to `/app/data/metrics.db` in K8s so SQLite lives on the PVC
- Helm `pre-upgrade` hook backs up `metrics.db` → `metrics.db.bak` before every deploy
- `prometheus.io/scrape: "true"` annotation on pod — Prometheus auto-discovers and scrapes this service

## CI/CD

GitHub Actions: `test → build-push (GHCR) → deploy → helm test`

Image tagged with git SHA. Set `KUBECONFIG` as a base64-encoded repository secret to enable deploy.
