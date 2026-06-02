"""Touchstone MCP server.

Wires every QA capability — DB, files, web, knowledge, playbooks,
notifications — into MCP tools the AI assistant can call.

## The credential-blindness contract (READ THIS)

The AI assistant sees:
  - Connection names ("prod-ro"), table names, SQL.
  - Channel names ("data-team-alerts"), credential names ("looker_admin").
  - PII-masked query results, audit-log summaries.

The AI assistant NEVER sees:
  - DB passwords, API keys, OAuth tokens, webhook URLs.
  - Raw values from a column classified as PII.
  - The contents of `secret://` / `env://` / `vault://` references.

All MCP tools enforce this by construction: they take *references* (names)
and resolve them server-side inside the gateway, after which any sensitive
intermediate value is dropped before the response is serialized.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

import click

from touchstone import Config
from touchstone.github_intel.activity import recent_changes_to, who_changed
from touchstone.github_intel.api import GitHubAPI
from touchstone.github_intel.owners import find_owners
from touchstone.knowledge.store import (
    KnowledgeStore,
    Note,
    Owner,
    Task,
    Decision,
)
from touchstone.notifications.sender import Notifier
from touchstone.playbooks import REGISTRY as PLAYBOOK_REGISTRY
from touchstone.qa.differ import diff_environments
from touchstone.qa.lineage import explain_lineage
from touchstone.qa.pr import FileChange, analyze_pr_data_impact
from touchstone.qa.profiler import profile_table
from touchstone.qa.test_gen import generate_test_cases
from touchstone.qa.validator import check_data_quality
from touchstone.secrets import resolve
from touchstone.security import (
    AuditLogger,
    ConsentGate,
    Gateway,
    Masker,
    PIIDetector,
    PolicyEngine,
    RateLimiter,
    ToolCallContext,
)
from touchstone.security.consent import TerminalChannel, WebhookChannel
from touchstone.types import TableRef
from touchstone.web.browser import BrowserSession, BrowserStep, CredentialRef


_active_snapshots: dict[str, list] = {}


def _build_app(config: Config):
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("touchstone")
    gateway = _build_gateway(config)
    knowledge = KnowledgeStore(config.knowledge.path)
    notifier = Notifier.from_config(config.notifications.channels)
    github_api = GitHubAPI()

    from touchstone.web.session_store import SessionStore
    session_store = SessionStore(base_dir=config.web.session_store_dir)

    def _browser(active_credential: str | None = None) -> BrowserSession:
        return BrowserSession(
            allowed_origins=config.web.allowed_origins,
            secret_resolver=resolve,
            credentials_config=config.web.credentials,
            session_store=session_store if active_credential else None,
            active_credential=active_credential,
            context_dir=config.web.context_dir,
            headless=config.web.headless,
        )

    # ====================================================================
    # DB / data tools
    # ====================================================================

    @app.tool()
    def list_connections() -> list[dict[str, Any]]:
        """List configured database / file connections (no credentials returned)."""
        return [
            {
                "name": c.name, "engine": c.engine.value,
                "database": c.database, "schema": c.schema_,
                "read_only": c.read_only, "tags": c.tags,
            }
            for c in config.connections.values()
        ]

    @app.tool()
    def list_tables(connection: str, schema: str | None = None) -> list[dict[str, str]]:
        """List tables in a connection."""
        out = gateway.execute(ToolCallContext(
            assistant_id=_aid(), assistant_session=_sid(), tenant_id=_tid(),
            tool="list_tables", connection=connection,
            sql=("SELECT table_schema, table_name FROM information_schema.tables"
                 + (f" WHERE table_schema = '{schema}'" if schema else "")),
        ))
        return [{"schema": str(r[0]) if r[0] else "", "name": str(r[1])}
                for r in out.masked.result.rows]

    @app.tool()
    def describe_table(connection: str, table: str, schema: str | None = None) -> list[dict[str, Any]]:
        """Describe a table's columns."""
        sf = f"AND table_schema = '{schema}'" if schema else ""
        out = gateway.execute(ToolCallContext(
            assistant_id=_aid(), assistant_session=_sid(), tenant_id=_tid(),
            tool="describe_table", connection=connection,
            sql=(f"SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                 f"WHERE table_name = '{table}' {sf} ORDER BY ordinal_position"),
        ))
        return [{"name": str(r[0]), "type": str(r[1]), "nullable": str(r[2]) == "YES"}
                for r in out.masked.result.rows]

    @app.tool()
    def query_database(connection: str, sql: str) -> dict[str, Any]:
        """Run a read-only SQL query (or JSON spec for Mongo). PII auto-masked.

        Note: the cost guard may auto-inject LIMIT, refuse cross-joins, or
        reject queries that EXPLAIN estimates above the configured row/byte
        limits. Any rewrite or warning lands in `warnings`.
        """
        out = gateway.execute(ToolCallContext(
            assistant_id=_aid(), assistant_session=_sid(), tenant_id=_tid(),
            tool="query_database", connection=connection, sql=sql,
        ))
        return {
            "columns": [{"name": c.name, "type": c.type} for c in out.masked.result.columns],
            "rows": out.masked.result.to_dicts(),
            "row_count": out.masked.result.row_count,
            "truncated": out.masked.result.truncated,
            "pii_findings_summary": out.audit_record.pii_summary,
            "latency_ms": out.masked.result.latency_ms,
            "snapshot_ts": out.snapshot_ts,
            "warnings": out.warnings,
        }

    @app.tool()
    def snapshot_begin(connection: str) -> dict[str, str]:
        """Open a snapshot transaction on `connection` and pin it for
        subsequent calls from the same assistant. TOCTOU defense — all
        queries within the snapshot see one consistent view.

        Returns the snapshot timestamp. Call `snapshot_end` when done.
        """
        cm = gateway.snapshot(tenant_id=_tid(), connection=connection)
        snap = cm.__enter__()
        _active_snapshots.setdefault(_aid(), []).append(cm)
        return {"connection": connection, "snapshot_ts": snap.snapshot_ts,
                "engine": snap.engine.value}

    @app.tool()
    def snapshot_end(connection: str) -> dict[str, str]:
        """Close the most recently opened snapshot."""
        stack = _active_snapshots.get(_aid(), [])
        if not stack:
            return {"status": "no_active_snapshot"}
        cm = stack.pop()
        cm.__exit__(None, None, None)
        return {"status": "closed", "connection": connection}

    @app.tool()
    def doctor() -> list[dict[str, Any]]:
        """Self-diagnose Touchstone's configuration. Read-only; safe."""
        from touchstone.diagnostics import run_doctor
        report = run_doctor()
        return [{"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix}
                for c in report.checks]

    @app.tool()
    def profile_table_tool(connection: str, table: str, schema: str | None = None,
                            top_k: int = 5, sample_n: int = 10) -> dict[str, Any]:
        """Stats / null rates / distincts / top-K / sample (PII-masked)."""
        return _to_jsonable(profile_table(
            gateway, assistant_id=_aid(), assistant_session=_sid(),
            connection=connection,
            table=TableRef(name=table, schema=schema), top_k=top_k, sample_n=sample_n,
        ))

    @app.tool()
    def diff_environments_tool(left_connection: str, right_connection: str, table: str,
                                schema: str | None = None,
                                primary_key: list[str] | None = None,
                                mode: str = "schema_and_rowcount") -> dict[str, Any]:
        """Compare a table across two connections."""
        return _to_jsonable(diff_environments(
            gateway, assistant_id=_aid(), assistant_session=_sid(),
            left_connection=left_connection, right_connection=right_connection,
            table=TableRef(name=table, schema=schema),
            primary_key=primary_key, mode=mode,
        ))

    @app.tool()
    def check_data_quality_tool(connection: str, expectations: dict[str, Any]) -> dict[str, Any]:
        """Run a YAML-style expectations spec."""
        return _to_jsonable(check_data_quality(
            gateway, assistant_id=_aid(), assistant_session=_sid(),
            connection=connection, expectations=expectations,
        ))

    @app.tool()
    def explain_lineage_tool(connection: str, column: str,
                              sql: str | None = None, dialect: str = "") -> dict[str, Any]:
        """Column-level lineage."""
        return _to_jsonable(explain_lineage(
            gateway, assistant_id=_aid(), assistant_session=_sid(),
            connection=connection, column=column, sql=sql, dialect=dialect,
        ))

    @app.tool()
    def analyze_pr_data_impact_tool(changes: list[dict[str, str]], dialect: str = "") -> dict[str, Any]:
        """Parse PR SQL diffs and predict downstream impact (no DB call)."""
        file_changes = [FileChange(**c) for c in changes]
        return _to_jsonable(analyze_pr_data_impact(changes=file_changes, dialect=dialect))

    @app.tool()
    def generate_test_cases_tool(connection: str, table: str,
                                  schema: str | None = None) -> dict[str, Any]:
        """Profile a table and propose data-quality expectations."""
        prof = profile_table(
            gateway, assistant_id=_aid(), assistant_session=_sid(),
            connection=connection, table=TableRef(name=table, schema=schema),
        )
        gen = generate_test_cases(prof)
        return {"table": gen.table, "expectations": gen.expectations,
                "rationale": gen.rationale, "yaml": gen.to_yaml()}

    # ====================================================================
    # Web automation tools
    # ====================================================================

    @app.tool()
    def list_allowed_origins() -> list[str]:
        """Return the origins the browser is allowed to visit."""
        return list(config.web.allowed_origins)

    @app.tool()
    def list_credential_refs() -> list[str]:
        """Return credential REFERENCE NAMES configured for browser auth.

        The AI sees names like `looker_admin`. The actual username/password
        is NEVER returned and never crosses the MCP boundary.
        """
        return sorted(config.web.credentials.keys())

    @app.tool()
    def browse(steps: list[dict[str, Any]],
                use_stored_session_for: str | None = None) -> dict[str, Any]:
        """Execute a sequence of browser steps in one session.

        Ops: navigate / click / fill / select / press / wait_for_selector /
        wait_for_url / wait_for_network_idle / wait_for_stable_rows /
        screenshot / extract_text / extract_table / list_buttons /
        list_inputs / login / bi_export / session_status.

        For fills against password-looking fields, set `credential: "<ref>"`
        instead of `value: "..."`. Touchstone refuses literal fills on
        password fields.

        If `use_stored_session_for` is set to a credential name, the session
        loads the encrypted storage_state captured by `touchstone session
        bootstrap`. This is the supported path for MFA-protected sites.

        Returns step results; sensitive values are scrubbed.
        """
        results = []
        with _browser(active_credential=use_stored_session_for) as br:
            for raw in steps:
                cred = raw.pop("credential", None)
                step = BrowserStep(
                    **{k: v for k, v in raw.items()
                       if k in {"op", "url", "selector", "value", "timeout_ms",
                                "field_role", "metadata"}},
                    credential=CredentialRef(cred) if cred else None,
                )
                results.append(_to_jsonable(br.execute(step)))
        return {"steps": results}

    @app.tool()
    def list_stored_sessions() -> list[str]:
        """Return credentials with a bootstrapped session. The AI uses this
        to know which sites it can hit without prompting for re-bootstrap."""
        return session_store.list_sessions()

    @app.tool()
    def session_is_valid(credential: str) -> dict[str, Any]:
        """Probe whether the stored session for a credential is still usable.
        Loads the session, navigates to the credential's login_url, checks
        whether we land on a login page. Returns {valid: bool, reason: str}."""
        spec = config.web.credentials.get(credential)
        if spec is None:
            return {"valid": False, "reason": f"no credential {credential!r}"}
        try:
            with _browser(active_credential=credential) as br:
                from touchstone.web.session_store import looks_like_login_page
                br._page.goto(spec.get("login_url") or spec.get("home_url") or "about:blank",
                              wait_until="domcontentloaded", timeout=15_000)
                title = br._page.title()
                if looks_like_login_page(br._page.url, title):
                    return {"valid": False, "reason": "landed on login page; re-bootstrap needed",
                            "final_url": br._page.url}
                return {"valid": True, "final_url": br._page.url}
        except Exception as e:
            return {"valid": False, "reason": str(e)}

    @app.tool()
    def verify_dashboard(
        dashboard_url: str, credential: str | None, table_selector: str,
        connection: str, sql: str, key_column: str, value_columns: list[str],
        tolerance: float = 0.0, login_url: str | None = None,
    ) -> dict[str, Any]:
        """Run the dashboard_verify playbook end-to-end."""
        from touchstone.playbooks.dashboard_verify import DashboardVerify
        pb = DashboardVerify(gateway=gateway, browser_factory=_browser)
        return _to_jsonable(pb.run(
            assistant_id=_aid(), assistant_session=_sid(), tenant_id=_tid(),
            dashboard_url=dashboard_url, credential=credential,
            table_selector=table_selector, connection=connection, sql=sql,
            key_column=key_column, value_columns=value_columns,
            tolerance=tolerance, login_url=login_url,
        ))

    # ====================================================================
    # Knowledge tools
    # ====================================================================

    @app.tool()
    def add_note(key: str, body: str, tags: list[str] | None = None,
                  author: str | None = None) -> dict[str, Any]:
        """Attach a note to a key (e.g., `table:orders`, `dashboard:looker/123`)."""
        n = knowledge.add_note(Note(id=None, key=key, body=body,
                                     tags=tags or [], author=author or _aid()))
        return _to_jsonable(n)

    @app.tool()
    def search_notes(query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Full-text search across knowledge notes."""
        return [_to_jsonable(n) for n in knowledge.search_notes(query, limit=limit)]

    @app.tool()
    def notes_for(key: str) -> list[dict[str, Any]]:
        """List notes attached to a specific key."""
        return [_to_jsonable(n) for n in knowledge.notes_for(key)]

    @app.tool()
    def who_owns(key_or_path: str) -> list[dict[str, Any]]:
        """Return owners for a knowledge-store key or a file path."""
        if key_or_path.startswith("path:") or "/" not in key_or_path:
            owners = knowledge.owners_for(key_or_path)
        else:
            owners = knowledge.who_owns_path(key_or_path)
        return [_to_jsonable(o) for o in owners]

    @app.tool()
    def add_task(title: str, body: str = "", owner: str | None = None,
                  due_at: str | None = None,
                  related: list[str] | None = None) -> dict[str, Any]:
        """Open a follow-up task."""
        return _to_jsonable(knowledge.add_task(Task(
            id=None, title=title, body=body, owner=owner,
            due_at=due_at, related=related or [],
        )))

    @app.tool()
    def list_open_tasks(owner: str | None = None) -> list[dict[str, Any]]:
        """List open / in-progress tasks."""
        return [_to_jsonable(t) for t in knowledge.open_tasks(owner=owner)]

    @app.tool()
    def close_task(task_id: int, status: str = "done") -> dict[str, str]:
        """Mark a task done / blocked / in_progress."""
        knowledge.update_task_status(task_id, status)
        return {"task_id": str(task_id), "status": status}

    @app.tool()
    def record_decision(title: str, rationale: str,
                         affects: list[str] | None = None,
                         decided_by: str | None = None) -> dict[str, Any]:
        """Capture an ADR-style decision."""
        return _to_jsonable(knowledge.add_decision(Decision(
            id=None, title=title, rationale=rationale,
            decided_by=decided_by or _aid(), affects=affects or [],
        )))

    @app.tool()
    def decisions_affecting(key: str) -> list[dict[str, Any]]:
        """Find recorded decisions that mention `key`."""
        return [_to_jsonable(d) for d in knowledge.decisions_affecting(key)]

    # ====================================================================
    # GitHub-intel tools
    # ====================================================================

    @app.tool()
    def github_recent_changes(repo: str, path: str, days: int = 30) -> list[dict[str, Any]]:
        """Commits touching a path in the last N days."""
        return [_to_jsonable(c) for c in recent_changes_to(github_api, repo, path, days=days)]

    @app.tool()
    def github_who_changed(repo: str, path: str, days: int = 90) -> dict[str, int]:
        """{author: commit_count} for commits touching `path`."""
        return who_changed(github_api, repo, path, days=days)

    @app.tool()
    def github_find_owners(repo: str, path: str) -> list[dict[str, Any]]:
        """Find owners — knowledge store first, then CODEOWNERS, then blame."""
        return [_to_jsonable(s) for s in find_owners(
            store=knowledge, api=github_api, repo=repo, path=path,
        )]

    # ====================================================================
    # Playbooks
    # ====================================================================

    @app.tool()
    def list_playbooks() -> list[str]:
        """Return the names of bundled playbooks."""
        return sorted(PLAYBOOK_REGISTRY.keys())

    @app.tool()
    def run_playbook(name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a playbook by name. `params` matches the playbook's `run()` signature."""
        cls = PLAYBOOK_REGISTRY.get(name)
        if cls is None:
            return {"error": f"unknown playbook: {name}"}
        kwargs = {
            "gateway": gateway, "knowledge": knowledge, "github_api": github_api,
            "browser_factory": _browser,
        }
        # Filter to fields the dataclass actually defines.
        accepted = {k: v for k, v in kwargs.items()
                    if k in getattr(cls, "__dataclass_fields__", {})}
        pb = cls(**accepted)
        report = pb.run(
            assistant_id=_aid(), assistant_session=_sid(), tenant_id=_tid(),
            **params,
        )
        return _to_jsonable(report)

    # ====================================================================
    # Notifications
    # ====================================================================

    @app.tool()
    def list_notification_channels() -> list[str]:
        """Return channel names. Webhook URLs / emails are NEVER returned."""
        return notifier.list_channels()

    @app.tool()
    def notify(channel: str, message: str,
                subject: str | None = None) -> dict[str, Any]:
        """Send a notification to a named channel.

        The AI cannot send to an arbitrary URL — only to channels the operator
        has configured. Operators may add a per-channel approval gate via the
        consent infrastructure.
        """
        result = notifier.send(channel, message, subject=subject)
        return {"ok": result.ok, "detail": result.detail, "channel": channel}

    # ====================================================================
    # Audit / introspection
    # ====================================================================

    @app.tool()
    def audit_query(limit: int = 20, since_seconds: int | None = None) -> list[dict[str, Any]]:
        """Return recent audit records (read-only; no DB hit)."""
        from pathlib import Path
        import json as _json

        path = None
        for sink in config.security.audit_sinks:
            if sink.get("kind") == "file":
                path = Path(sink["path"]).expanduser()
                break
        if not path or not path.exists():
            return []
        with path.open() as f:
            lines = f.readlines()
        records = [_json.loads(line) for line in lines[-limit:]]
        return records

    return app


def _build_gateway(config: Config) -> Gateway:
    policy = PolicyEngine.from_files(config.security.policy_files)
    pii = PIIDetector(
        threshold=config.security.pii_threshold,
        enabled=config.security.pii_detectors_enabled,
    )
    masker = Masker(default_strategy=config.security.pii_default_strategy)
    rate_limiter = RateLimiter(per_minute=config.security.rate_limit_per_minute)
    audit = AuditLogger.from_config(config.security.audit_sinks)
    consent = ConsentGate(channel=(
        WebhookChannel(os.environ["TOUCHSTONE_CONSENT_WEBHOOK"])
        if "TOUCHSTONE_CONSENT_WEBHOOK" in os.environ
        else TerminalChannel()
    ))
    return Gateway(
        config=config, policy=policy, pii=pii, masker=masker,
        consent=consent, rate_limiter=rate_limiter, audit=audit,
    )


def _aid() -> str:
    return os.environ.get("TOUCHSTONE_ASSISTANT_ID", "anonymous")


def _sid() -> str:
    return os.environ.get("TOUCHSTONE_SESSION_ID", "default")


def _to_jsonable(obj: Any) -> Any:
    from dataclasses import is_dataclass
    from enum import Enum
    from pathlib import Path

    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_to_jsonable(v) for v in obj]
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    return obj


@click.command()
@click.option("--config", "config_path", default=None)
@click.option("--transport", default="stdio",
              type=click.Choice(["stdio", "sse", "streamable-http"]))
def main(config_path: str | None, transport: str) -> None:
    """Run the Touchstone MCP server."""
    config = Config.load(config_path)
    app = _build_app(config)
    app.run(transport=transport)


if __name__ == "__main__":
    main()
