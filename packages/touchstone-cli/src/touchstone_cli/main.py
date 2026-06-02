"""Touchstone CLI.

Core commands (DB QA):
    touchstone init
    touchstone connections
    touchstone profile <conn> <table>
    touchstone diff <left> <right> <table>
    touchstone check <conn> <expectations.yaml>
    touchstone pr --repo X --pr N
    touchstone audit verify | tail
    touchstone serve-mcp

Extended commands (broader QA):
    touchstone browse <steps.json>
    touchstone knowledge note add <key> <body>
    touchstone knowledge note search <q>
    touchstone knowledge tasks
    touchstone knowledge sync codeowners --repo X
    touchstone knowledge sync prs --repo X
    touchstone who <repo> <path>
    touchstone playbook list
    touchstone playbook run <name> --params <json>
    touchstone notify <channel> <message>
    touchstone llm ask <prompt>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from touchstone import Config
from touchstone.qa.differ import diff_environments
from touchstone.qa.profiler import profile_table
from touchstone.qa.test_gen import generate_test_cases
from touchstone.qa.validator import check_data_quality
from touchstone.security.audit import verify_chain
from touchstone.types import TableRef

from touchstone_cli.gateway import build_gateway
from touchstone_cli.init_wizard import run_init
from touchstone_cli.render import (
    render_diff,
    render_profile,
    render_test_suggestions,
    render_validation_report,
)


console = Console()


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--config", "config_path", default=None, envvar="TOUCHSTONE_CONFIG")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """Touchstone — safe, audited QA tooling for data work."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


# -- init / introspection --------------------------------------------------

@cli.command()
@click.option("--non-interactive", is_flag=True)
def init(non_interactive: bool) -> None:
    """Interactive setup wizard."""
    target = run_init(non_interactive=non_interactive)
    console.print(f"[green]Wrote config to[/green] {target}")


@cli.command()
@click.pass_context
def connections(ctx: click.Context) -> None:
    """List configured connections."""
    cfg = Config.load(ctx.obj["config_path"])
    t = Table(title="Connections")
    t.add_column("Name"); t.add_column("Engine"); t.add_column("Database")
    t.add_column("Tags"); t.add_column("Read-only")
    for c in cfg.connections.values():
        t.add_row(c.name, c.engine.value, c.database or "-",
                  ",".join(c.tags) or "-", "yes" if c.read_only else "no")
    console.print(t)


# -- core QA ---------------------------------------------------------------

@cli.command()
@click.argument("connection")
@click.argument("table")
@click.option("--schema", default=None)
@click.option("--top-k", default=5)
@click.option("--sample", "sample_n", default=10)
@click.option("--json", "as_json", is_flag=True)
@click.option("--suggest-tests", is_flag=True)
@click.pass_context
def profile(ctx, connection, table, schema, top_k, sample_n, as_json, suggest_tests):
    """Profile a table."""
    cfg = Config.load(ctx.obj["config_path"])
    gw = build_gateway(cfg)
    prof = profile_table(
        gw, assistant_id="cli", assistant_session="cli",
        connection=connection, table=TableRef(name=table, schema=schema),
        top_k=top_k, sample_n=sample_n,
    )
    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(asdict(prof), default=str, indent=2))
        return
    render_profile(console, prof)
    if suggest_tests:
        render_test_suggestions(console, generate_test_cases(prof))


@cli.command()
@click.argument("left")
@click.argument("right")
@click.argument("table")
@click.option("--schema", default=None)
@click.option("--primary-key", "primary_key", multiple=True)
@click.option("--group-by", default=None)
@click.option("--mode", default="schema_and_rowcount",
              type=click.Choice(["schema", "schema_and_rowcount", "full"]))
@click.pass_context
def diff(ctx, left, right, table, schema, primary_key, group_by, mode):
    """Diff a table across two connections."""
    cfg = Config.load(ctx.obj["config_path"])
    gw = build_gateway(cfg)
    result = diff_environments(
        gw, assistant_id="cli", assistant_session="cli",
        left_connection=left, right_connection=right,
        table=TableRef(name=table, schema=schema),
        primary_key=list(primary_key) or None,
        group_by=group_by, mode=mode,
    )
    render_diff(console, result)


@cli.command()
@click.argument("connection")
@click.argument("expectations_path", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def check(ctx, connection, expectations_path):
    """Run a YAML expectations file."""
    cfg = Config.load(ctx.obj["config_path"])
    gw = build_gateway(cfg)
    report = check_data_quality(
        gw, assistant_id="cli", assistant_session="cli",
        connection=connection, expectations_path=Path(expectations_path),
    )
    render_validation_report(console, report)
    sys.exit(0 if report.passed else 1)


@cli.command()
@click.option("--repo", required=True)
@click.option("--pr", "pr_number", required=True, type=int)
@click.option("--dialect", default="")
@click.option("--dbt-manifest", "dbt_manifest_path", type=click.Path())
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def pr(ctx, repo, pr_number, dialect, dbt_manifest_path, as_json):
    """Generate a PR data-impact report from a GitHub PR."""
    from touchstone_cli.github_fetch import fetch_pr_sql_changes
    from touchstone.qa.pr import analyze_pr_data_impact
    from dataclasses import asdict

    changes = fetch_pr_sql_changes(repo, pr_number)
    if not changes:
        console.print(f"[yellow]No SQL changes in {repo}#{pr_number}[/yellow]")
        return
    report = analyze_pr_data_impact(
        changes=changes, dialect=dialect,
        dbt_manifest=Path(dbt_manifest_path) if dbt_manifest_path else None,
    )
    if as_json:
        click.echo(json.dumps(asdict(report), default=str, indent=2))
        return
    from touchstone_cli.render import render_pr_report
    render_pr_report(console, repo, pr_number, report)


# -- audit -----------------------------------------------------------------

@cli.group()
def audit():
    """Audit-log operations."""


@audit.command("verify")
@click.option("--file", "audit_file", default="~/.touchstone/audit.jsonl",
              type=click.Path())
def audit_verify(audit_file):
    """Verify the hash chain."""
    path = Path(audit_file).expanduser()
    if not path.exists():
        console.print(f"[red]Audit file not found:[/red] {path}")
        sys.exit(1)
    ok, seen, err = verify_chain(path)
    if ok:
        console.print(f"[green]Chain valid.[/green] {seen} records verified.")
    else:
        console.print(f"[red]Chain broken at record {seen}:[/red] {err}")
        sys.exit(2)


@audit.command("tail")
@click.option("--file", "audit_file", default="~/.touchstone/audit.jsonl",
              type=click.Path())
@click.option("-n", "lines", default=20)
def audit_tail(audit_file, lines):
    """Show the most recent audit records."""
    path = Path(audit_file).expanduser()
    if not path.exists():
        console.print(f"[red]Audit file not found:[/red] {path}")
        sys.exit(1)
    with path.open() as f:
        all_lines = f.readlines()
    for line in all_lines[-lines:]:
        rec = json.loads(line)
        verdict = rec.get("policy_verdict", {}).get("verdict", "")
        click.echo(f"{rec.get('ts', ''):28}  {verdict:18}  "
                   f"{rec.get('tool', ''):28}  {rec.get('connection', '')}")


# -- web automation --------------------------------------------------------

@cli.command()
@click.argument("steps_path", type=click.Path(exists=True))
@click.option("--credential", default=None,
              help="Use the encrypted stored session for this credential.")
@click.pass_context
def browse(ctx, steps_path, credential):
    """Run a sequence of browser steps from a JSON file."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.web.browser import BrowserSession, BrowserStep, CredentialRef
    from touchstone.web.session_store import SessionStore
    from touchstone.secrets import resolve
    from dataclasses import asdict

    steps = json.loads(Path(steps_path).read_text())
    session_store = SessionStore(base_dir=cfg.web.session_store_dir) if credential else None
    with BrowserSession(
        allowed_origins=cfg.web.allowed_origins,
        secret_resolver=resolve,
        credentials_config=cfg.web.credentials,
        session_store=session_store,
        active_credential=credential,
        context_dir=cfg.web.context_dir,
        headless=cfg.web.headless,
    ) as br:
        for raw in steps:
            cred = raw.pop("credential", None)
            step = BrowserStep(
                **{k: v for k, v in raw.items()
                   if k in {"op", "url", "selector", "value", "timeout_ms",
                            "field_role", "metadata"}},
                credential=CredentialRef(cred) if cred else None,
            )
            result = br.execute(step)
            click.echo(json.dumps(asdict(result), default=str))


# -- knowledge -------------------------------------------------------------

@cli.group()
def knowledge():
    """Knowledge store — notes / owners / tasks / decisions / PRs."""


@knowledge.group("note")
def knowledge_note():
    """Notes attached to keys."""


@knowledge_note.command("add")
@click.argument("key")
@click.argument("body")
@click.option("--tag", "tags", multiple=True)
@click.option("--author", default="cli")
@click.pass_context
def kn_add(ctx, key, body, tags, author):
    """Attach a note."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.knowledge.store import KnowledgeStore, Note
    store = KnowledgeStore(cfg.knowledge.path)
    n = store.add_note(Note(id=None, key=key, body=body,
                             tags=list(tags), author=author))
    console.print(f"[green]added note #{n.id}[/green] on {key}")


@knowledge_note.command("search")
@click.argument("query")
@click.option("--limit", default=20)
@click.pass_context
def kn_search(ctx, query, limit):
    """FTS over notes."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.knowledge.store import KnowledgeStore
    store = KnowledgeStore(cfg.knowledge.path)
    for n in store.search_notes(query, limit=limit):
        console.print(f"[bold]{n.key}[/bold]  [{n.created_at}]  {n.body[:160]}")


@knowledge.command("tasks")
@click.option("--owner", default=None)
@click.pass_context
def kn_tasks(ctx, owner):
    """List open tasks."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.knowledge.store import KnowledgeStore
    store = KnowledgeStore(cfg.knowledge.path)
    t = Table(title="Open tasks")
    t.add_column("ID"); t.add_column("Status"); t.add_column("Owner")
    t.add_column("Title"); t.add_column("Due")
    for task in store.open_tasks(owner=owner):
        t.add_row(str(task.id), task.status, task.owner or "-",
                  task.title, task.due_at or "-")
    console.print(t)


@knowledge.group("sync")
def knowledge_sync():
    """Sync from external sources (CODEOWNERS, GitHub PRs)."""


@knowledge_sync.command("codeowners")
@click.option("--repo", required=True, help="owner/repo")
@click.pass_context
def kn_sync_co(ctx, repo):
    """Pull CODEOWNERS into the owners table."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.knowledge.store import KnowledgeStore
    from touchstone.knowledge.github_sync import sync_codeowners
    store = KnowledgeStore(cfg.knowledge.path)
    n = sync_codeowners(store, repo)
    console.print(f"[green]added {n} owner rule(s) from {repo}/CODEOWNERS[/green]")


@knowledge_sync.command("prs")
@click.option("--repo", required=True)
@click.option("--limit", default=100)
@click.pass_context
def kn_sync_prs(ctx, repo, limit):
    """Pull recent merged PRs into the PR digest."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.knowledge.store import KnowledgeStore
    from touchstone.knowledge.github_sync import sync_recent_prs
    store = KnowledgeStore(cfg.knowledge.path)
    n = sync_recent_prs(store, repo, limit=limit)
    console.print(f"[green]added {n} PR digest record(s) from {repo}[/green]")


# -- who -------------------------------------------------------------------

@cli.command()
@click.argument("repo")
@click.argument("path")
@click.option("--days", default=90)
@click.pass_context
def who(ctx, repo, path, days):
    """Who owns / recently changed a path?"""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.github_intel.api import GitHubAPI
    from touchstone.github_intel.owners import find_owners
    from touchstone.github_intel.activity import who_changed, recent_changes_to
    from touchstone.knowledge.store import KnowledgeStore

    store = KnowledgeStore(cfg.knowledge.path)
    api = GitHubAPI()

    owners = find_owners(store=store, api=api, repo=repo, path=path)
    console.print(f"[bold]Owners for {path}[/bold]")
    for s in owners:
        console.print(f"  @{s.handle:25s} {s.role:14s} {s.source:20s} conf={s.confidence:.2f}")

    counts = who_changed(api, repo, path, days=days)
    console.print(f"\n[bold]Recent committers (last {days} days)[/bold]")
    for handle, n in counts.items():
        console.print(f"  @{handle:25s} {n} commit(s)")

    changes = recent_changes_to(api, repo, path, days=days)
    console.print(f"\n[bold]Last {min(len(changes), 10)} commit(s)[/bold]")
    for c in changes[:10]:
        console.print(f"  {c.sha}  {c.when}  @{c.author}  {c.message[:80]}")


# -- playbooks -------------------------------------------------------------

@cli.group()
def playbook():
    """Run pre-canned QA playbooks."""


@playbook.command("list")
def pb_list():
    """List bundled playbooks."""
    from touchstone.playbooks import REGISTRY
    for name in sorted(REGISTRY):
        console.print(f"  {name}")


@playbook.command("run")
@click.argument("name")
@click.option("--params", "params_json", required=True,
              help='JSON object of params, e.g. \'{"connection":"local-pg","table":"orders"}\'')
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def pb_run(ctx, name, params_json, as_json):
    """Run a playbook by name."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.playbooks import REGISTRY
    from touchstone.knowledge.store import KnowledgeStore
    from touchstone.github_intel.api import GitHubAPI
    from touchstone.web.browser import BrowserSession
    from touchstone.secrets import resolve

    gw = build_gateway(cfg)
    store = KnowledgeStore(cfg.knowledge.path)
    api = GitHubAPI()

    def _browser():
        return BrowserSession(
            allowed_origins=cfg.web.allowed_origins, secret_resolver=resolve,
            credentials_config=cfg.web.credentials,
            context_dir=cfg.web.context_dir, headless=cfg.web.headless,
        )

    cls = REGISTRY.get(name)
    if cls is None:
        console.print(f"[red]unknown playbook: {name}[/red]")
        sys.exit(1)
    kwargs = {"gateway": gw, "knowledge": store, "github_api": api,
              "browser_factory": _browser}
    accepted = {k: v for k, v in kwargs.items()
                if k in getattr(cls, "__dataclass_fields__", {})}
    pb_inst = cls(**accepted)
    report = pb_inst.run(assistant_id="cli", assistant_session="cli",
                          **json.loads(params_json))
    if as_json:
        from dataclasses import asdict
        click.echo(json.dumps(asdict(report), default=str, indent=2))
    else:
        console.print(report.to_markdown())


# -- notify ----------------------------------------------------------------

@cli.command()
@click.argument("channel")
@click.argument("message")
@click.option("--subject", default=None)
@click.pass_context
def notify(ctx, channel, message, subject):
    """Send a notification to a configured channel."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.notifications.sender import Notifier
    notifier = Notifier.from_config(cfg.notifications.channels)
    result = notifier.send(channel, message, subject=subject)
    if result.ok:
        console.print(f"[green]sent to {channel}[/green]")
    else:
        console.print(f"[red]send failed:[/red] {result.detail}")
        sys.exit(1)


# -- llm -------------------------------------------------------------------

@cli.command("llm-ask")
@click.argument("prompt")
@click.pass_context
def llm_ask(ctx, prompt):
    """One-shot prompt to the configured LLM (handy for sanity-checking auth)."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.llm.adapter import build_llm, LLMMessage
    llm = build_llm({
        "provider": cfg.llm.provider, "model": cfg.llm.model,
        "api_key_ref": cfg.llm.api_key_ref, "extra": cfg.llm.extra,
    })
    if llm is None:
        console.print("[red]No LLM provider configured (set [llm] in touchstone.toml)[/red]")
        sys.exit(1)
    resp = llm.chat([LLMMessage(role="user", content=prompt)])
    console.print(resp.content)


# -- session bootstrap (MFA / SSO) -----------------------------------------

@cli.group()
def session():
    """Manage browser session state (MFA / SSO bootstrap)."""


@session.command("bootstrap")
@click.argument("credential")
@click.option("--headless/--headed", default=False,
              help="Run headed (default) so the operator can complete MFA.")
@click.pass_context
def session_bootstrap(ctx, credential, headless):
    """Open a browser, let the operator log in (including MFA), save
    encrypted session state for headless reuse.

    Requires TOUCHSTONE_SESSION_KEY to be set. The AI never sees this key
    or the captured cookies.
    """
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.web.bootstrap import bootstrap_session
    spec = cfg.web.credentials.get(credential)
    if spec is None:
        console.print(f"[red]No credential {credential!r} in config.[/red]")
        sys.exit(1)
    result = bootstrap_session(
        credential_name=credential, credential_config=spec,
        base_dir=cfg.web.session_store_dir, headless=headless,
    )
    console.print(f"[green]Saved session for {credential}[/green]")
    console.print(f"  path:    {result.session_path}")
    console.print(f"  cookies: {result.captured_cookies}, origins: {result.captured_origins}")
    console.print(f"  url:     {result.final_url}")


@session.command("list")
@click.pass_context
def session_list(ctx):
    """List credentials with stored sessions."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.web.session_store import SessionStore
    ss = SessionStore(base_dir=cfg.web.session_store_dir)
    names = ss.list_sessions()
    if not names:
        console.print("[yellow]No stored sessions.[/yellow]")
        return
    for n in names:
        console.print(f"  {n}")


@session.command("delete")
@click.argument("credential")
@click.pass_context
def session_delete(ctx, credential):
    """Delete a stored session (forces re-bootstrap next time)."""
    cfg = Config.load(ctx.obj["config_path"])
    from touchstone.web.session_store import SessionStore
    SessionStore(base_dir=cfg.web.session_store_dir).delete(credential)
    console.print(f"[green]deleted session for {credential}[/green]")


# -- doctor ----------------------------------------------------------------

@cli.command()
@click.pass_context
def doctor(ctx):
    """Diagnose common misconfigurations."""
    from touchstone.diagnostics import run_doctor
    report = run_doctor(ctx.obj["config_path"])
    failed = 0
    for c in report.checks:
        marker = "[green]ok[/green]   " if c.ok else "[red]fail[/red] "
        console.print(f"  {marker} {c.name:30s}  {c.detail}")
        if not c.ok:
            failed += 1
            if c.fix:
                console.print(f"           [dim]fix: {c.fix}[/dim]")
    if failed:
        console.print(f"\n[red]{failed} check(s) failed.[/red]")
        sys.exit(1)
    console.print(f"\n[green]all {len(report.checks)} checks passed.[/green]")


# -- audit verify rotated --------------------------------------------------

@audit.command("verify-rotated")
@click.option("--dir", "audit_dir", default="~/.touchstone",
              type=click.Path(), help="Directory containing audit.jsonl + rotated files.")
@click.option("--base", default="audit.jsonl",
              help="Base name (rotations are appended as base.<ts>).")
def audit_verify_rotated(audit_dir, base):
    """Verify chain across a rotated audit log set."""
    from touchstone.security.audit import verify_rotated_chain
    d = Path(audit_dir).expanduser()
    files = sorted(d.glob(f"{base}.*")) + [d / base]
    files = [f for f in files if f.exists()]
    if not files:
        console.print(f"[red]No audit files in {d}[/red]")
        sys.exit(1)
    ok, total, err = verify_rotated_chain(files)
    if ok:
        console.print(f"[green]Chain valid across {len(files)} file(s).[/green] {total} records.")
    else:
        console.print(f"[red]Chain broken: {err}[/red]")
        sys.exit(2)


# -- serve-mcp -------------------------------------------------------------

@cli.command("serve-mcp")
@click.option("--transport", default="stdio",
              type=click.Choice(["stdio", "sse", "streamable-http"]))
@click.pass_context
def serve_mcp(ctx, transport):
    """Convenience: run the MCP server with this CLI's config."""
    from touchstone_mcp.server import _build_app
    cfg = Config.load(ctx.obj["config_path"])
    app = _build_app(cfg)
    app.run(transport=transport)


if __name__ == "__main__":
    cli()
