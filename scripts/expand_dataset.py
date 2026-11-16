"""Build the 200+ incident dataset and the held-out eval set.

    python -m scripts.expand_dataset

Inputs (all hand-written, fictional):
  data/sample_logs.json       20 base incidents
  data/families_extra.json    27 base incidents, each with a held-out log
  data/eval_queries.json      hand-written held-out logs (+ extras added below)

Output:
  data/incidents_expanded.json   every base incident + N-1 synthetic recurrences
                                 (synthetic: true) with different services, hosts,
                                 file paths, line numbers, versions and values
  data/eval_set.json             held-out logs, never indexed

Deterministic: fixed seed, so re-running produces identical files.
"""

import json
import random
import re
from pathlib import Path

VARIANTS_PER_FAMILY = 5  # 1 base + 4 synthetic recurrences
SEED = 42

SERVICES = [
    "orders-api", "billing-service", "inventory-api", "checkout", "search-api",
    "notifications-worker", "user-service", "auth-gateway", "reports-api",
    "pricing-engine", "shipping-service", "catalog-service", "ledger-sync",
    "payments-worker", "media-service", "analytics-ingest", "admin-portal",
    "recommendations", "fraud-scorer", "subscriptions-api",
]
CI_NAMES = ["web-frontend CI", "platform CI", "infra CI", "release CI", "mobile CI", "data CI"]

# Held-out logs for original families that had no eval query yet.
EXTRA_HOLDOUTS = [
    ("docker-no-space", "pip install, disk full on runner",
     "#9 [stage-1 4/6] RUN npm ci --omit=dev\n#9 18.7 npm ERR! code ENOSPC\n#9 18.7 npm ERR! syscall write\n#9 18.7 npm ERR! errno -28\n#9 18.7 npm ERR! nospc ENOSPC: no space left on device, write\nERROR: failed to solve: process \"/bin/sh -c npm ci --omit=dev\" did not complete successfully: exit code: 228"),
    ("pytest-timezone-flaky", "date assertion off by one day",
     "FAILED tests/test_billing.py::test_invoice_due_date - AssertionError: assert '2025-11-01' == '2025-10-31'\n  - 2025-10-31\n  + 2025-11-01\n1 failed, 388 passed in 52.10s"),
    ("pg-migration-deadlock", "deadlock during schema migration",
     "django.db.utils.OperationalError: deadlock detected\nDETAIL:  Process 9921 waits for AccessExclusiveLock on relation 18874 of database 16384; blocked by process 9875.\nProcess 9875 waits for RowShareLock on relation 18880 of database 16384; blocked by process 9921.\n  Applying orders.0057_add_status_index..."),
    ("lambda-timeout", "lambda timed out on outbound call",
     "2025-08-20T04:11:59.880Z c0ffee00-4444-4e4e-8f8f-3a3a3a3a3a3a Task timed out after 15.02 seconds\nREPORT RequestId: c0ffee00-4444-4e4e-8f8f-3a3a3a3a3a3a Duration: 15015.33 ms Billed Duration: 15000 ms Memory Size: 512 MB Max Memory Used: 101 MB"),
    ("ts-strict-null", "TS2532 possibly undefined",
     "src/pages/Orders.tsx(88,21): error TS2532: Object is possibly 'undefined'.\nsrc/pages/Orders.tsx(91,9): error TS18048: 'order.customer' is possibly 'undefined'.\nFound 2 errors in the same file, starting at: src/pages/Orders.tsx:88"),
    ("github-rate-limit", "GitHub API secondary rate limit",
     "Error: You have exceeded a secondary rate limit. Please wait a few minutes before you try again.\n    at /home/runner/work/_actions/acme/label-sync/v1/dist/index.js:812:15\n  status: 403, headers: { 'retry-after': '60', 'x-ratelimit-remaining': '0' }"),
]

# Logs with no matching family: the system should say "no close match".
NO_MATCH = [
    ("novel: rust borrow checker", "error[E0382]: borrow of moved value: `config`\n  --> src/main.rs:41:20\n   |\n38 |     let server = Server::new(config);\n   |                              ------ value moved here\n41 |     println!(\"{:?}\", config.port);\n   |                    ^^^^^^^^^^^ value borrowed here after move"),
    ("novel: iOS code signing", "error: No profiles for 'com.acme.shopper' were found: Xcode couldn't find any iOS App Development provisioning profiles matching 'com.acme.shopper'. (in target 'Shopper' from project 'Shopper')\n** ARCHIVE FAILED **"),
    ("novel: gradle dependency not found", "FAILURE: Build failed with an exception.\n* What went wrong:\nExecution failed for task ':app:checkDebugAarMetadata'.\n> Could not resolve all files for configuration ':app:debugRuntimeClasspath'.\n   > Could not find com.acme.android:analytics-sdk:3.2.1."),
    ("novel: utf-8 decode error in CSV", "Traceback (most recent call last):\n  File \"/app/importer/parse.py\", line 18, in read_rows\n    for row in csv.DictReader(f):\n  File \"<frozen codecs>\", line 322, in decode\nUnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"),
    ("novel: cron script permission denied", "/etc/cron.daily/rotate-exports:\n/opt/acme/bin/rotate-exports.sh: line 12: /var/exports/archive/2025-09-01.tar.gz: Permission denied\nrun-parts: /etc/cron.daily/rotate-exports exited with return code 1"),
]


def _rand_ts(rng: random.Random) -> str:
    return f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"


def mutate(base: dict, rng: random.Random) -> dict:
    """A plausible recurrence of the same bug somewhere else in the fleet."""
    old_svc = base["service"]
    is_ci = old_svc.endswith(" CI")
    new_svc = rng.choice([s for s in (CI_NAMES if is_ci else SERVICES) if s != old_svc])
    old_slug = old_svc.replace(" CI", "").split("-")[0]
    new_slug = new_svc.replace(" CI", "").replace("-", "_").split("_")[0]

    log = base["log_text"]
    if not is_ci:
        log = log.replace(old_svc, new_svc)
        # file paths like /app/orders/routes.py -> /app/billing/routes.py
        log = re.sub(rf"(/(?:app|src|srv)/){re.escape(old_slug)}(?=[/_])", rf"\g<1>{new_slug}", log)
    log = re.sub(r"(line )(\d+)", lambda m: f"{m.group(1)}{rng.randint(10, 400)}", log)
    log = re.sub(r"(\.(?:java|go|py|ts|tsx|js)[:(])(\d+)", lambda m: f"{m.group(1)}{rng.randint(10, 400)}", log)
    log = re.sub(r"\b10\.\d+\.\d+\.\d+\b", lambda m: f"10.{rng.randint(0, 9)}.{rng.randint(0, 255)}.{rng.randint(2, 254)}", log)
    log = re.sub(r"\b20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z\b", lambda m: _rand_ts(rng), log)
    log = re.sub(r"python3\.1\d", lambda m: f"python3.{rng.choice([10, 11, 12])}", log)
    log = re.sub(r"\b(size|overflow|Memory Size:|memory:) (\d+)", lambda m: f"{m.group(1)} {int(m.group(2)) * rng.choice([1, 2, 4])}", log)

    out = dict(base)
    out.pop("holdout_log", None)
    out.update(
        service=new_svc,
        log_text=log,
        title=f"{base['title']} ({new_svc})",
        root_cause=base["root_cause"].replace(old_svc, new_svc),
        synthetic=True,
    )
    return out


def main() -> None:
    rng = random.Random(SEED)
    bases = json.load(open("data/sample_logs.json")) + json.load(open("data/families_extra.json"))
    families = {b["family"] for b in bases}
    assert len(families) == len(bases), "one base incident per family"

    incidents = []
    for base in bases:
        clean = {k: v for k, v in base.items() if k != "holdout_log"}
        incidents.append({**clean, "synthetic": False})
        for _ in range(VARIANTS_PER_FAMILY - 1):
            incidents.append(mutate(base, rng))

    eval_set = json.load(open("data/eval_queries.json"))
    eval_set += [
        {"name": f"{b['family']} (holdout)", "expected_family": b["family"], "log": b["holdout_log"]}
        for b in bases if "holdout_log" in b
    ]
    eval_set += [{"name": n, "expected_family": f, "log": log} for f, n, log in EXTRA_HOLDOUTS]
    eval_set += [{"name": n, "expected_family": None, "log": log} for n, log in NO_MATCH]
    assert all(q["expected_family"] in families for q in eval_set if q["expected_family"])

    # Guard against leakage: no eval log may be identical to an indexed log.
    indexed = {i["log_text"] for i in incidents}
    assert not any(q["log"] in indexed for q in eval_set)

    Path("data/incidents_expanded.json").write_text(json.dumps(incidents, indent=2) + "\n")
    Path("data/eval_set.json").write_text(json.dumps(eval_set, indent=2) + "\n")
    covered = {q["expected_family"] for q in eval_set if q["expected_family"]}
    print(f"families: {len(families)}  incidents: {len(incidents)} "
          f"({sum(i['synthetic'] for i in incidents)} synthetic)")
    print(f"eval queries: {len(eval_set)}  matchable: {sum(1 for q in eval_set if q['expected_family'])}  "
          f"no-match: {sum(1 for q in eval_set if not q['expected_family'])}  families covered: {len(covered)}/{len(families)}")


if __name__ == "__main__":
    main()
