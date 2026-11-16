# Incident Triage Assistant

Paste a stack trace or failed CI log → get a plain-English root cause, severity, and
suggested fix, grounded in similar past incidents (RAG over Postgres + pgvector, answered by Claude).

**Status:** in progress — see [ROADMAP.md](ROADMAP.md). Full results and architecture land in Phase 7.

## Quick start (Phase 1)

```bash
docker compose up -d db                      # Postgres + pgvector on :5438
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.seed --reset     # index data/sample_logs.json
.venv/bin/python -m scripts.eval_retrieval   # hit@1 / hit@3 / MRR + latency
.venv/bin/uvicorn app.main:app --port 8030   # GET /health, POST /index-incident
```

## Docs
[Requirements](docs/REQUIREMENTS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Decisions](docs/DECISIONS.md) · [Implementation plan](docs/IMPLEMENTATION_PLAN.md) ·
[Eval plan](docs/EVAL_PLAN.md)
