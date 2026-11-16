# Roadmap

Status: ✅ done · 🔄 in progress · ⬜ not started
Task-level detail: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)

| # | Phase | Status | Outcome |
|---|---|---|---|
| 0 | Docs | ✅ | Requirements, architecture, decisions, plan, eval plan |
| 1 | Core search | ✅ | pgvector store, seed, `/index-incident`, retrieval eval |
| 2 | Bigger dataset + hybrid search | ⬜ | 200+ incidents, vector + keyword search compared |
| 3 | Claude triage + tests | 🔄 | `/triage`, redaction, citation guard, pytest suite |
| 4 | Triage quality eval | ⬜ | Severity / citation / root-cause accuracy numbers |
| 5 | Feedback loop + noise reduction | ⬜ | `/feedback`, dedup, `/stats`, LLM calls saved |
| 6 | Cloud workflow | ⬜ | S3 → Lambda → `/triage` → S3, CloudWatch on Moto |
| 7 | CI + publish | ⬜ | GitHub Actions, latency bench, README with results |

## Docs
- [Requirements](docs/REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Design decisions](docs/DECISIONS.md)
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Evaluation plan](docs/EVAL_PLAN.md)
