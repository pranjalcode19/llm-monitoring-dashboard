import os
import time
import json
import sqlite3
from datetime import datetime
from openai import OpenAI
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

client = OpenAI(
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434") + "/v1",
    api_key="ollama"
)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# SQLite for persistent metrics
def get_db():
    conn = sqlite3.connect("metrics.db")
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

    conn = get_db()
    conn.execute(
        "INSERT INTO requests (timestamp, question, answer, latency_ms, model, status) VALUES (?,?,?,?,?,?)",
        (datetime.utcnow().isoformat(), body.question, answer, latency_ms, "llama3.2", status)
    )
    conn.commit()
    conn.close()

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

@app.get("/", response_class=HTMLResponse)
def dashboard():
    with open("dashboard.html") as f:
        return f.read()
