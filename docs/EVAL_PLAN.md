# Evaluation plan

Three things get measured: **retrieval** (does search find the right past incident?),
**triage quality** (is Claude's answer right?), and **latency/cost**. Every reported
number comes from a script in `scripts/` and is saved to `results/`.

---

## 1. Datasets

| File | Contents | Used for |
|---|---|---|
| `data/sample_logs.json` | 20 hand-written base incidents | Source |
| `data/families_extra.json` | 27 hand-written base incidents, each with a held-out log | Source |
| `data/eval_queries.json` | 15 hand-written held-out logs | Source |
| `data/incidents_expanded.json` | 235 incidents: 47 families × (1 base + 4 synthetic recurrences, `synthetic: true`) | Index (Phase 2+) |
| `data/eval_set.json` | 53 held-out logs **never indexed**, each with `expected_family` (or `null` = no real match) | All evals |

All incidents are fictional. `scripts/expand_dataset.py` builds the last two files deterministically.

Rules:
- Eval logs are new occurrences of known bugs: different service names, hosts, values and line numbers, not copies.
- At least ~10% of eval queries have no real match, to test that the system says "no close match" instead of forcing one.
- Eval queries are never added to the index, so evaluation can't leak.

---

## 2. Retrieval eval — `scripts/eval_retrieval.py`

| Metric | Definition |
|---|---|
| hit@1 | Share of matchable queries whose top result is from the expected **family** (any recurrence counts) |
| hit@3 | … in the top 3 |
| MRR | Mean of 1/rank of the first result from the expected family (0 if not in top 10) |
| No-match precision (Phase 2+) | Share of no-match queries where every result is below `MIN_SIMILARITY` |
| Similarity gap | Mean top-1 similarity for matchable vs no-match queries (tells us where to set the threshold) |
| Latency | Embed and search p50 / p95 (ms), warm, after one warm-up call |

Run per mode in Phase 2: `--mode vector | keyword | hybrid`.
**Targets:** hit@1 ≥ 80%, hit@3 ≥ 90%, search p95 < 200 ms.

---

## 3. Triage quality eval — `scripts/eval_triage.py` (Phase 5)

Each eval query gets ground truth: expected incident, expected severity, and 2–4
**key facts** the root cause must mention (e.g. "connection leak", "sessions not closed").

| Metric | How it's scored |
|---|---|
| Severity exact | predicted == expected |
| Severity within ±1 | \|score − expected score\| ≤ 1 |
| Citation recall | expected incident ∈ `matched_incident_ids` (matchable queries) |
| Citation precision | cited IDs that are correct / all cited IDs |
| No-match honesty | for no-match queries: `matched_incident_ids` empty and confidence < 0.5 |
| Root-cause score | LLM judge, rubric 0–2 per key fact (0 missing, 1 vague, 2 clear) → normalised 0–1 |
| Fix actionable | judge yes/no: names a concrete change (config key, code change, command) |
| Calibration | mean confidence on correct vs incorrect answers |

**Judge:** a separate Claude call with the rubric, the expected facts and the triage
output, using structured output (`{fact_scores: [...], actionable: bool, rationale}`).
To check the judge itself, a sample of ~10 judgements is read by hand and agreement is noted.

**Targets:** severity exact ≥ 70% / ±1 ≥ 95%, citation recall ≥ 85%, root-cause score ≥ 0.8.

**Prompt changes:** one baseline run, one tuned run, both saved. A change is kept only if
it doesn't lower any metric beyond noise (re-run once if the difference is small).

**Cost:** estimated before running (queries × (triage + judge) tokens) and confirmed first.

---

## 4. Latency + cost — `scripts/bench.py` (Phase 8)

- N = 30 warm requests per log type through the real HTTP API, after 3 warm-up calls
- Report p50 / p95 / max for `embed`, `search`, `llm`, `total` (from the response's `latency_ms`) plus client-side wall time
- Input/output tokens per triage and the resulting cost per triage at list price
- Run per model if more than one is evaluated
- The <2 s target is checked against **total p50**; if it's missed, the README says so and shows the per-step breakdown

---

## 5. Noise reduction — `scripts/replay_noise.py` (Phase 6)

- Generate a burst of logs from a small number of distinct failures (e.g. 500 logs, 12 failures) with randomised timestamps, request IDs and IPs
- Replay through `/triage` and report: total logs, LLM calls made, calls avoided by dedup, % reduction, time saved (avoided calls × mean LLM latency)

---

## 6. In CI (Phase 8)

- Unit tests (no key, no network)
- Seed + retrieval eval against a pgvector service container; **fail if hit@3 drops below the committed baseline minus 5 points**
- Triage eval is **not** run in CI (needs a key and costs money); run manually and commit the results

---

## 7. Reporting format

Every script writes `results/<eval>_<YYYY-MM-DD>.json`:
```json
{ "run_at": "...", "git_sha": "...", "config": { "model": "...", "mode": "hybrid", "top_k": 3 },
  "dataset": { "indexed": 214, "queries": 50 },
  "metrics": { "hit@1": 0.84, "hit@3": 0.94, "mrr": 0.89 },
  "per_query": [ { "name": "...", "expected": "...", "rank": 1, "top_similarity": 0.91 } ] }
```
and prints a short table to the terminal. The README links to the specific result files.
