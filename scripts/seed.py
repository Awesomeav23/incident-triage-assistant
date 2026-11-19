"""Load past incidents from JSON into Postgres.

    python -m scripts.seed --reset              # data/incidents_expanded.json (235)
    python -m scripts.seed --file data/sample_logs.json   # just the 20 base incidents
"""

import argparse
import json
import time

from app.models import IncidentIn
from app.rag import IncidentStore, get_embedder


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="data/incidents_expanded.json")
    p.add_argument("--reset", action="store_true", help="delete existing incidents first")
    args = p.parse_args()

    incidents = [IncidentIn(**row) for row in json.load(open(args.file))]
    store = IncidentStore()
    if args.reset:
        store.truncate()

    t0 = time.perf_counter()
    vectors = get_embedder().embed_many([i.log_text for i in incidents])
    t1 = time.perf_counter()
    for inc, vec in zip(incidents, vectors, strict=True):
        store.add(inc, vec)
    t2 = time.perf_counter()

    print(f"indexed {len(incidents)} incidents from {args.file}")
    print(f"embed {t1 - t0:.2f}s  insert {t2 - t1:.2f}s  total in DB: {store.count()}")


if __name__ == "__main__":
    main()
