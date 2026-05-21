import os
import time
import sqlite3
from datetime import datetime
from openai import OpenAI
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

client = OpenAI(
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
    api_key="ollama"
)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# DB_PATH env var lets K8s set this to the PVC mount path (/app/data/metrics.db)
# Falls back to local path for running outside K8s
DB_PATH = os.getenv("DB_PATH", "metrics.db")

# Prometheus metrics
# Counter: only goes up — total requests broken down by status (success/error)
REQUEST_COUNT = Counter(
    "llm_requests_total",
    "Total LLM requests",
    ["status"]           # label: each status gets its own counter
)
# Histogram: tracks latency distribution across buckets
# buckets = the SLA boundaries you care about (100ms, 500ms, 1s, 2s, 5s, 10s)
REQUEST_LATENCY = Histogram(
    "llm_request_latency_seconds",
    "LLM request latency in seconds",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, float("inf")]
)
# Counter: errors only — useful for alerting (error rate = errors / total)
ERROR_COUNT = Counter(
    "llm_errors_total",
    "Total LLM errors"
)

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            question TEXT,
            answer TEXT,
            latency_ms INTEGER,
            model TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

class Question(BaseModel):
    question: str

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ask")
def ask(body: Question):
    start = time.time()
    status = "success"
    answer = ""
    try:
        response = client.chat.completions.create(
            model="llama3.2",
            messages=[
                {"role": "system", "content": "You are a helpful DevOps assistant."},
                {"role": "user", "content": body.question}
            ]
        )
        answer = response.choices[0].message.content
    except Exception as e:
        answer = str(e)
        status = "error"

    latency_ms = round((time.time() - start) * 1000)

    # Record to SQLite (human-readable history)
    conn = get_db()
    conn.execute(
        "INSERT INTO requests (timestamp, question, answer, latency_ms, model, status) VALUES (?,?,?,?,?,?)",
        (datetime.utcnow().isoformat(), body.question, answer, latency_ms, "llama3.2", status)
    )
    conn.commit()
    conn.close()

    # Record to Prometheus (time-series, scrapeable by Prometheus server)
    REQUEST_COUNT.labels(status=status).inc()
    REQUEST_LATENCY.observe(latency_ms / 1000)
    if status == "error":
        ERROR_COUNT.inc()

    return {"answer": answer, "latency_ms": latency_ms, "status": status}

@app.get("/metrics/raw")
def metrics_raw():
    conn = get_db()
    rows = conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.get("/metrics/summary")
def metrics_summary():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    avg_latency = conn.execute("SELECT AVG(latency_ms) FROM requests").fetchone()[0] or 0
    errors = conn.execute("SELECT COUNT(*) FROM requests WHERE status='error'").fetchone()[0]
    recent = conn.execute(
        "SELECT timestamp, latency_ms, status FROM requests ORDER BY id DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return {
        "total_requests": total,
        "avg_latency_ms": round(avg_latency),
        "error_count": errors,
        "success_rate": f"{((total - errors) / total * 100):.0f}%" if total else "0%",
        "recent": [dict(r) for r in recent]
    }

# Prometheus scrape endpoint — returns metrics in Prometheus text format
# Prometheus server calls this every 15s (configured in prometheus.yml)
@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("dashboard.html") as f:
        return f.read()
