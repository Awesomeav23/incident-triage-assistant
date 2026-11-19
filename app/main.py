"""FastAPI routes: /triage, /feedback, /stats, /index-incident, /incidents, /samples, /health."""

import json
import time
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException, Query

from app import metrics
from app.config import settings
from app.history import PgRunStore, RunStore, fingerprint, guess_error_type, guess_title
from app.llm import LLMConfigError, LLMError, Triager, get_triager
from app.models import (
    SEVERITY_SCORE,
    FeedbackIn,
    FeedbackOut,
    IncidentDetail,
    IncidentIn,
    IncidentPage,
    IndexedIncident,
    Sample,
    Severity,
    Stats,
    TriageRequest,
    TriageResponse,
)
from app.rag import Embedder, IncidentStore, get_embedder
from app.redact import redact


@lru_cache(maxsize=1)
def get_store() -> IncidentStore:
    return IncidentStore()


@lru_cache(maxsize=1)
def get_runs() -> RunStore:
    return PgRunStore(get_store())


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm up so the first request doesn't pay model-load / connect cost.
    if not app.dependency_overrides:
        get_store()
        get_runs()
        get_embedder().embed("warmup")
    yield


app = FastAPI(title="Incident Triage Assistant", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(store: IncidentStore = Depends(get_store)):
    mode = "claude" if settings.llm_mode == "claude" else "demo"
    return {"status": "ok", "incidents_indexed": store.count(), "llm_mode": mode}


@app.get("/incidents", response_model=IncidentPage)
def list_incidents(
    q: str | None = Query(None, max_length=100),
    severity: Severity | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_synthetic: bool = True,
    store: IncidentStore = Depends(get_store),
):
    items, total = store.list(q=q, severity=severity.value if severity else None,
                              limit=limit, offset=offset, include_synthetic=include_synthetic)
    return IncidentPage(items=items, total=total)


@app.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, store: IncidentStore = Depends(get_store)):
    inc = store.get(incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


SAMPLES_FILE = "data/samples.json"


@lru_cache(maxsize=1)
def load_samples() -> list[Sample]:
    return [Sample(**s) for s in json.load(open(SAMPLES_FILE))]


@app.get("/samples", response_model=list[Sample])
def samples():
    """Held-out example logs for the UI's "Try a sample" buttons (never indexed)."""
    return load_samples()


@app.post("/index-incident", response_model=IndexedIncident, status_code=201)
def index_incident(
    inc: IncidentIn,
    store: IncidentStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
):
    inc.log_text, _ = redact(inc.log_text)  # never store secrets
    # Embed only the log, the same input /triage embeds, so queries and docs match.
    vec = embedder.embed(inc.log_text)
    return IndexedIncident(id=store.add(inc, vec), title=inc.title)


@app.post("/triage", response_model=TriageResponse)
def triage(
    req: TriageRequest,
    store: IncidentStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
    triager: Triager = Depends(get_triager),
    runs: RunStore = Depends(get_runs),
):
    t0 = time.perf_counter()
    log, redacted = redact(req.log)  # secrets never reach the embedder, DB or LLM

    # Same failure seen recently? Reuse that answer: no search, no model call (ADR-010).
    fp = fingerprint(log)
    cached = runs.find_recent(fp, triager.model, settings.dedup_window_min)
    if cached is not None:
        count = runs.bump(cached["id"])
        total = round((time.perf_counter() - t0) * 1000, 1)
        r = cached["result"]
        metrics.put_triage_metrics({"total": total, "llm": 0.0}, r["severity_score"],
                                   len(r["matched_incident_ids"]), duplicate=True)
        return TriageResponse(**{
            **cached["result"],
            "redacted_secrets": redacted,
            "latency_ms": {"embed": 0.0, "search": 0.0, "llm": 0.0, "total": total},
            "run_id": cached["id"], "duplicate_of": cached["id"], "duplicate_count": count,
        })

    vec = embedder.embed(log)
    t1 = time.perf_counter()
    similar = store.search(
        vec, req.top_k, mode=settings.search_mode, query_text=log,
        min_similarity=settings.min_similarity, distinct_families=True,
    )
    t2 = time.perf_counter()
    try:
        result = triager.triage(log, similar)
    except LLMConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    t3 = time.perf_counter()

    # Never let the model cite an incident it wasn't shown.
    shown = {s.id for s in similar}
    result.matched_incident_ids = [i for i in result.matched_incident_ids if i in shown]

    latency = {
        "embed": round((t1 - t0) * 1000, 1),
        "search": round((t2 - t1) * 1000, 1),
        "llm": round((t3 - t2) * 1000, 1),
        "total": round((t3 - t0) * 1000, 1),
    }
    score = SEVERITY_SCORE[result.severity]
    metrics.put_triage_metrics(latency, score, len(result.matched_incident_ids))
    resp = TriageResponse(
        **result.model_dump(),
        severity_score=score,
        similar_incidents=similar,
        no_close_match=not similar,
        search_mode=settings.search_mode,
        redacted_secrets=redacted,
        latency_ms=latency,
        model=triager.model,
    )
    stored = resp.model_dump(mode="json", exclude={"run_id", "duplicate_of", "duplicate_count"})
    resp.run_id = runs.record(fp, log, stored, triager.model, latency)
    return resp


@app.post("/feedback", response_model=FeedbackOut, status_code=201)
def feedback(
    fb: FeedbackIn,
    runs: RunStore = Depends(get_runs),
    store: IncidentStore = Depends(get_store),
    embedder: Embedder = Depends(get_embedder),
):
    """Mark a triage right or wrong; optionally add it to the library as a new incident."""
    run = runs.get(fb.triage_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Triage run not found")
    if runs.has_feedback(fb.triage_run_id):
        raise HTTPException(status_code=409, detail="This triage already has feedback")

    indexed_as = None
    if fb.index:
        r = run["result"]
        family = None
        if fb.correct and r["matched_incident_ids"]:
            # Confirmed match → this log is another recurrence of that incident's failure.
            cited = r["matched_incident_ids"][0]
            family = next((s["family"] for s in r["similar_incidents"] if s["id"] == cited), None)
        inc = IncidentIn(
            title=fb.title or guess_title(run["log_text"]),
            service=fb.service or "unknown",
            error_type=guess_error_type(run["log_text"]),
            log_text=run["log_text"],  # already redacted when the run was stored
            root_cause=(fb.root_cause or "").strip() or r["root_cause_summary"],
            fix=(fb.fix or "").strip() or r["suggested_fix"],
            severity=fb.severity or Severity(r["severity"]),
            tags=["from-feedback"],
            family=family,
        )
        indexed_as = store.add(inc, embedder.embed(inc.log_text))
    fid = runs.add_feedback(fb, indexed_as)
    return FeedbackOut(id=fid, triage_run_id=fb.triage_run_id, correct=fb.correct, indexed_as=indexed_as)


@app.get("/stats", response_model=Stats)
def stats(
    recent: int = Query(10, ge=0, le=50),
    runs: RunStore = Depends(get_runs),
    store: IncidentStore = Depends(get_store),
):
    return Stats(incidents_indexed=store.count(), **runs.stats(recent))
