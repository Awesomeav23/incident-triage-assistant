# Incident Triage Assistant

Paste a stack trace or failed CI log → get a plain-English root cause, severity, and
suggested fix, grounded in similar past incidents (RAG over Postgres + pgvector, answered by Claude).

**Status:** in progress — see [ROADMAP.md](ROADMAP.md). Full results and architecture land in Phase 8.

![Triage result](docs/screenshots/triage.png)

## Quick start

Everything runs locally and free: demo mode makes no Claude calls, and AWS is emulated by Moto.

```bash
docker compose up -d db moto                   # Postgres + pgvector :5438, AWS emulator :4567
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m scripts.expand_dataset     # build 235 incidents + 53-log eval set
.venv/bin/python -m scripts.seed --reset       # index them
.venv/bin/python -m pytest                     # 137 tests, no API key or AWS account needed

cp .env.example .env                           # LLM_MODE=demo by default: free, no Claude calls
                                               # LLM_MODE=claude + ANTHROPIC_API_KEY for real triage (billed)
.venv/bin/uvicorn app.main:app --port 8030 --env-file .env   # API docs: http://localhost:8030/docs

cd frontend && npm install && npm run dev      # web UI: http://localhost:5181
npm test                                       # 32 frontend tests
```

**Or everything in containers:** `docker compose --profile app up -d --build` → UI on :5181, API on :8030
(stop the local uvicorn/Vite first, or set `API_PORT` / `WEB_PORT`).

**Cloud workflow (S3 → Lambda → triage → S3):** with the API running,
`.venv/bin/python -m scripts.simulate_s3_ingest` uploads sample logs to the emulated bucket,
runs the Lambda handler on each upload, and lists the results and CloudWatch metrics.

**Evals (free):** `python -m scripts.eval_retrieval` (search quality), `python -m scripts.eval_triage`
(answer quality, demo mode), `python -m scripts.replay_noise` (calls avoided by duplicate reuse).

## Docs
[Requirements](docs/REQUIREMENTS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Decisions](docs/DECISIONS.md) · [Implementation plan](docs/IMPLEMENTATION_PLAN.md) ·
[Eval plan](docs/EVAL_PLAN.md)
