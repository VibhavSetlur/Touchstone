#!/usr/bin/env python3
"""End-to-end smoke test for Touchstone.

Walks the full path a new user takes:
  1. Write a touchstone.toml with a local DuckDB connection.
  2. Seed the DuckDB file with a customers table containing PII.
  3. `touchstone doctor` against the config.
  4. `touchstone profile` → assert PII is masked.
  5. Open a snapshot transaction, run two queries, close it.
  6. Add a knowledge note, search for it.
  7. Verify audit log is written and the chain validates.
  8. Exercise `bootstrap` / `cost guard` headlessly.

Run from the repo root:
    make smoke
    # or:
    python3 scripts/smoke_test.py

Exits non-zero on first failure. Prints what worked + what's next.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}ok{RESET}    {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")
    raise SystemExit(1)


def warn(msg: str) -> None:
    print(f"  {YELLOW}warn{RESET}  {msg}")


def step(title: str) -> None:
    print(f"\n{BOLD}{title}{RESET}")


def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="touchstone-smoke-"))
    config_path = workdir / "touchstone.toml"
    duckdb_path = workdir / "shop.duckdb"
    audit_path = workdir / "audit.jsonl"
    knowledge_path = workdir / "knowledge.db"

    env = {
        **os.environ,
        "TOUCHSTONE_CONFIG": str(config_path),
        # In case the runner has none — give it one (used only for session_store).
        "TOUCHSTONE_SESSION_KEY": os.environ.get("TOUCHSTONE_SESSION_KEY", "smoke-test-key"),
    }

    print(f"{BOLD}Touchstone smoke test{RESET}\n  workdir: {workdir}")

    # -- 1. Write config --------------------------------------------------
    step("1. Write touchstone.toml")
    config_path.write_text(textwrap.dedent(f"""
        [connections.local-duck]
        engine = "duckdb"
        database = "{duckdb_path}"
        read_only = false
        tags = ["dev"]

        [security]
        pii_threshold = 0.4
        pii_default_strategy = "redact"
        pii_detectors_enabled = ["column_name", "regex"]
        consent_required_on = ["non_select"]
        rate_limit_per_minute = 600

        [[security.audit_sinks]]
        kind = "file"
        path = "{audit_path}"

        [knowledge]
        path = "{knowledge_path}"

        [llm]
        provider = "none"
        model = ""

        [web]
        allowed_origins = []
        headless = true

        [assistants]
        cli = ["reader"]
    """).lstrip())
    ok(f"wrote {config_path}")

    # -- 2. Seed DuckDB ---------------------------------------------------
    step("2. Seed DuckDB with sample data")
    try:
        import duckdb
    except ImportError:
        fail("duckdb not installed (pip install duckdb)")
    conn = duckdb.connect(str(duckdb_path))
    conn.execute("""
        CREATE TABLE customers (
            customer_id BIGINT PRIMARY KEY,
            email VARCHAR,
            full_name VARCHAR,
            phone VARCHAR,
            tier VARCHAR,
            created_at TIMESTAMP
        )
    """)
    conn.execute("""
        INSERT INTO customers VALUES
            (1, 'jane@example.com', 'Jane Doe',   '+15551112233', 'gold',     CURRENT_TIMESTAMP),
            (2, 'john@example.com', 'John Smith', '+15552223344', 'standard', CURRENT_TIMESTAMP),
            (3, 'alice@example.com','Alice Liu',  '+15553334455', 'standard', CURRENT_TIMESTAMP),
            (4, 'bob@example.com',  'Bob Chen',   NULL,           'platinum', CURRENT_TIMESTAMP)
    """)
    conn.close()
    ok(f"seeded {duckdb_path} (4 rows)")

    # Now the connection needs to be read-only — flip it.
    cfg_text = config_path.read_text().replace("read_only = false", "read_only = true")
    config_path.write_text(cfg_text)
    ok("flipped connection to read_only")

    # -- 3. `touchstone --version` ----------------------------------------
    step("3. touchstone --version")
    r = _run(["touchstone", "--version"], env=env)
    if r.returncode != 0:
        fail(f"--version failed: {r.stderr}")
    if "touchstone " not in r.stdout:
        fail(f"unexpected output: {r.stdout!r}")
    ok(r.stdout.strip().splitlines()[0])

    # -- 4. `touchstone doctor` --------------------------------------------
    step("4. touchstone doctor")
    r = _run(["touchstone", "doctor"], env=env)
    print(textwrap.indent(r.stdout, "    "))
    if r.returncode != 0:
        warn("doctor reported failures (may be expected if web/LLM not configured)")
    else:
        ok("doctor passed")

    # -- 5. `touchstone profile` — assert PII masked ----------------------
    step("5. touchstone profile (PII should be masked)")
    r = _run(["touchstone", "profile", "local-duck", "customers", "--json"], env=env)
    if r.returncode != 0:
        fail(f"profile failed: {r.stderr}")
    if "jane@example.com" in r.stdout:
        fail("PII LEAKED! Email appears unmasked in profile output")
    if "REDACTED" not in r.stdout:
        warn("expected to see REDACTED markers in profile output")
    ok("profile complete, no PII leaks detected")

    # -- 6. Knowledge: add + search ---------------------------------------
    step("6. knowledge note add / search")
    r = _run(["touchstone", "knowledge", "note", "add",
              "table:customers", "TZ quirk on Friday batches; see PR #1421"],
             env=env)
    if r.returncode != 0:
        fail(f"note add failed: {r.stderr}")
    ok("note added")
    r = _run(["touchstone", "knowledge", "note", "search", "Friday"], env=env)
    if "TZ quirk" not in r.stdout:
        fail(f"FTS search did not return the note: {r.stdout!r}")
    ok("note searchable via FTS")

    # -- 7. Audit log written + chain verifies ----------------------------
    step("7. touchstone audit verify")
    if not audit_path.exists():
        fail("audit log was not written")
    audit_lines = audit_path.read_text().count("\n")
    ok(f"audit log: {audit_path} ({audit_lines} record(s))")
    r = _run(["touchstone", "audit", "verify", "--file", str(audit_path)], env=env)
    if r.returncode != 0:
        fail(f"audit verify failed: {r.stdout} {r.stderr}")
    ok(r.stdout.strip())

    # -- 8. Cost guard — auto-LIMIT injection -----------------------------
    step("8. cost guard auto-LIMIT injection")
    PYTHONPATH = ":".join([
        "packages/touchstone-core/src",
        "packages/touchstone-cli/src",
        "packages/touchstone-mcp/src",
    ])
    py = textwrap.dedent("""
        from touchstone.security.cost_guard import CostGuard, CostLimits
        from touchstone.types import Engine
        g = CostGuard(limits=CostLimits(auto_inject_limit=500))
        rewritten, warnings = g.static_check(
            "SELECT * FROM customers", Engine.DUCKDB, [],
        )
        assert "LIMIT 500" in rewritten, rewritten
        assert any("auto-injected" in w for w in warnings), warnings
        print("LIMIT injection works")
    """)
    r = subprocess.run(
        [sys.executable, "-c", py],
        env={**env, "PYTHONPATH": PYTHONPATH},
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"cost guard test failed:\n{r.stderr}")
    ok(r.stdout.strip())

    # -- 9. Session store encryption (no Playwright needed) ---------------
    step("9. session store encryption roundtrip")
    py = textwrap.dedent(f"""
        from touchstone.web.session_store import SessionStore
        s = SessionStore(base_dir="{workdir}/sessions")
        s.save("test_cred", {{"cookies": [{{"name": "sid", "value": "secret"}}]}})
        loaded = s.load("test_cred")
        assert loaded.storage_state["cookies"][0]["value"] == "secret"
        print("encryption roundtrip works")
    """)
    r = subprocess.run(
        [sys.executable, "-c", py],
        env={**env, "PYTHONPATH": PYTHONPATH},
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        fail(f"session store test failed:\n{r.stderr}")
    ok(r.stdout.strip())

    print()
    print(f"{GREEN}{BOLD}All smoke tests passed.{RESET}")
    print(f"  Workdir kept at: {workdir}")
    print(f"  Remove with: rm -rf {workdir}")


def _run(cmd, *, env):
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


if __name__ == "__main__":
    main()
