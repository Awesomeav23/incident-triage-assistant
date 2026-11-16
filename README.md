# Incident Triage Assistant

Paste a stack trace or failed CI log → get a plain-English root cause, severity, and
suggested fix, grounded in similar past incidents (RAG over Postgres + pgvector, answered by Claude).

**Status:** in progress — see [ROADMAP.md](ROADMAP.md). Full results and architecture land in Phase 8.

## Quick start

```bash
docker compose up -d db                        # Postgres + pgvector on :5438
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m scripts.expand_dataset     # build 235 incidents + 53-log eval set
.venv/bin/python -m scripts.seed --reset       # index them
.venv/bin/python -m scripts.eval_retrieval     # compare vector / keyword / hybrid search
.venv/bin/python -m pytest                     # 51 tests, no API key needed

cp .env.example .env                           # add ANTHROPIC_API_KEY for real triage
.venv/bin/uvicorn app.main:app --port 8030 --env-file .env   # then open http://localhost:8030/docs
```

## Docs
[Requirements](docs/REQUIREMENTS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Decisions](docs/DECISIONS.md) · [Implementation plan](docs/IMPLEMENTATION_PLAN.md) ·
[Eval plan](docs/EVAL_PLAN.md)
