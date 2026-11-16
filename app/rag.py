"""Embeddings (fastembed, local ONNX) and incident search: vector, keyword, or hybrid."""

import re
from contextlib import contextmanager
from functools import lru_cache

import numpy as np
import psycopg2
from pgvector.psycopg2 import register_vector
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.config import settings
from app.models import IncidentIn, SimilarIncident

SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS incidents (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    service     TEXT NOT NULL,
    error_type  TEXT NOT NULL,
    log_text    TEXT NOT NULL,
    root_cause  TEXT NOT NULL,
    fix         TEXT NOT NULL,
    severity    TEXT NOT NULL,
    tags        TEXT[] NOT NULL DEFAULT '{{}}',
    embedding   vector({settings.embed_dim}) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS incidents_embedding_hnsw
    ON incidents USING hnsw (embedding vector_cosine_ops);

-- Phase 2: family label, synthetic flag, keyword search
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS family TEXT;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS synthetic BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS search_text TEXT NOT NULL DEFAULT '';
ALTER TABLE incidents ADD COLUMN IF NOT EXISTS search_tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', search_text)) STORED;
CREATE INDEX IF NOT EXISTS incidents_search_gin ON incidents USING gin (search_tsv);
"""

RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATES = 20  # depth taken from each ranker before fusion
SEARCH_MODES = ("vector", "keyword", "hybrid")

# ---------- log normalisation ----------
# Strip run-specific noise so two occurrences of the same bug embed close together.
_NOISE = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?\b"), "<ts>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<addr>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{12,40}\b", re.I), "<hash>"),
    (re.compile(r"[ \t]+"), " "),
]
MAX_EMBED_CHARS = 4000  # bge-small truncates at 512 tokens anyway
# Only for over-budget logs: one giant line (minified JSON, base64) shouldn't eat it all.
# Normal error lines are never cut: their key words are often at the end.
MAX_LINE_CHARS = 1000
HEAD_SHARE = 0.65  # rest of the budget goes to the tail


def normalize_log(text: str) -> str:
    for pattern, repl in _NOISE:
        text = pattern.sub(repl, text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if sum(len(ln) + 1 for ln in lines) <= MAX_EMBED_CHARS:
        return "\n".join(lines)
    lines = [ln[:MAX_LINE_CHARS] for ln in lines]
    # Too long: keep the head (error message) and the tail (innermost frames,
    # "Caused by"), each within its own budget so neither crowds the other out.
    head, used = [], 0
    for ln in lines:
        if used + len(ln) + 1 > MAX_EMBED_CHARS * HEAD_SHARE:
            break
        head.append(ln)
        used += len(ln) + 1
    tail, used_tail = [], 0
    for ln in reversed(lines[len(head):]):
        if used + used_tail + len(ln) + 1 > MAX_EMBED_CHARS - 4:
            break
        tail.insert(0, ln)
        used_tail += len(ln) + 1
    return "\n".join(head + ["..."] + tail)


# ---------- keyword tokens ----------
# The same tokenizer builds the stored search_text and the query, so both sides match.
_TOKEN = re.compile(r"[a-z][a-z0-9]{2,}|\b\d{3,4}\b")
_STOP = frozenset("""
the and for with from not this that was are has have had but its into via per
error errors line file most recent call last traceback exception raise raised
self none true false null true info warn warning debug ago over
""".split())
MAX_QUERY_TOKENS = 64


def tokens(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for tok in _TOKEN.findall(normalize_log(text).lower()):
        if tok not in _STOP:
            seen.setdefault(tok)
    return list(seen)


def keyword_query(text: str) -> str | None:
    """OR-query of distinctive tokens; tokens are [a-z0-9] only, so safe for to_tsquery."""
    toks = tokens(text)[:MAX_QUERY_TOKENS]
    return " | ".join(toks) if toks else None


# ---------- embeddings ----------


class Embedder:
    def __init__(self, model_name: str = settings.embed_model):
        from fastembed import TextEmbedding  # heavy import, keep lazy

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> np.ndarray:
        return next(iter(self._model.embed([normalize_log(text)])))

    def embed_many(self, texts: list[str]) -> list[np.ndarray]:
        return list(self._model.embed([normalize_log(t) for t in texts]))


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder()


# ---------- database ----------


class IncidentStore:
    def __init__(self, dsn: str = settings.database_url):
        # Schema first (the vector type must exist before register_vector).
        boot = psycopg2.connect(dsn)
        with boot, boot.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        boot.close()
        self._pool = ThreadedConnectionPool(1, 10, dsn)
        self._registered: set[int] = set()

    @contextmanager
    def conn(self):
        c = self._pool.getconn()
        if id(c) not in self._registered:
            register_vector(c)
            self._registered.add(id(c))
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            self._pool.putconn(c)

    def add(self, inc: IncidentIn, embedding: np.ndarray) -> int:
        search_text = " ".join(tokens(f"{inc.title}\n{inc.error_type}\n{inc.log_text}"))
        with self.conn() as c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO incidents
                   (title, service, error_type, log_text, root_cause, fix, severity, tags,
                    family, synthetic, search_text, embedding)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (inc.title, inc.service, inc.error_type, inc.log_text, inc.root_cause,
                 inc.fix, inc.severity.value, inc.tags, inc.family, inc.synthetic,
                 search_text, embedding),
            )
            return cur.fetchone()[0]

    _COLUMNS = "i.id, i.title, i.service, i.error_type, i.root_cause, i.fix, i.severity, i.family"

    def search(
        self,
        embedding: np.ndarray,
        top_k: int,
        mode: str = "vector",
        query_text: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[SimilarIncident]:
        """Top-k incidents. `similarity` is always cosine similarity, whatever the ranking.

        vector  - pgvector cosine distance
        keyword - Postgres full-text (ts_rank over an OR-query of log tokens)
        hybrid  - reciprocal rank fusion of both: score = sum(1 / (RRF_K + rank))
        Results with cosine similarity below min_similarity are dropped.
        """
        if mode not in SEARCH_MODES:
            raise ValueError(f"mode must be one of {SEARCH_MODES}")
        tsq = keyword_query(query_text) if query_text and mode != "vector" else None
        if mode == "keyword" and tsq is None:
            return []

        if mode == "vector" or tsq is None:
            sql = f"""SELECT {self._COLUMNS}, 1 - (i.embedding <=> %(q)s) AS similarity,
                             1 - (i.embedding <=> %(q)s) AS score
                      FROM incidents i ORDER BY i.embedding <=> %(q)s LIMIT %(k)s"""
        elif mode == "keyword":
            sql = f"""SELECT {self._COLUMNS}, 1 - (i.embedding <=> %(q)s) AS similarity,
                             ts_rank(i.search_tsv, to_tsquery('simple', %(tsq)s)) AS score
                      FROM incidents i
                      WHERE i.search_tsv @@ to_tsquery('simple', %(tsq)s)
                      ORDER BY score DESC, i.id LIMIT %(k)s"""
        else:  # hybrid
            sql = f"""
                WITH vec AS (
                    SELECT id, row_number() OVER (ORDER BY embedding <=> %(q)s) AS r
                    FROM incidents ORDER BY embedding <=> %(q)s LIMIT %(n)s
                ), kw AS (
                    SELECT id, row_number() OVER (
                        ORDER BY ts_rank(search_tsv, to_tsquery('simple', %(tsq)s)) DESC, id) AS r
                    FROM incidents WHERE search_tsv @@ to_tsquery('simple', %(tsq)s)
                    ORDER BY r LIMIT %(n)s
                ), fused AS (
                    SELECT id, sum(1.0 / (%(rrf)s + r)) AS score
                    FROM (SELECT * FROM vec UNION ALL SELECT * FROM kw) u GROUP BY id
                )
                SELECT {self._COLUMNS}, 1 - (i.embedding <=> %(q)s) AS similarity, f.score
                FROM fused f JOIN incidents i USING (id)
                ORDER BY f.score DESC, similarity DESC LIMIT %(k)s"""

        params = {"q": embedding, "k": top_k, "tsq": tsq, "n": CANDIDATES, "rrf": RRF_K}
        with self.conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [SimilarIncident(**r) for r in rows if r["similarity"] >= min_similarity]

    def count(self) -> int:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM incidents")
            return cur.fetchone()[0]

    def truncate(self) -> None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("TRUNCATE incidents RESTART IDENTITY")
