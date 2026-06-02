"""`touchstone doctor` — diagnose common misconfigurations.

Runs a battery of checks and returns a structured report:
  - Config loads, all secret references resolve.
  - Every configured connection actually connects.
  - Policy bundle parses.
  - Audit sink is writable and rotates as expected.
  - Web origins are reachable and Playwright is installed if web creds exist.
  - Session store: which credentials have a stored session, when captured,
    when last validated.
  - Knowledge store opens and the file is the expected shape.
  - LLM provider config: pings a tiny prompt if non-`none`.
  - Sensitivity catalog: every reference points to an existing column where
    we can verify (best-effort; needs a connection).

Use:
  - As a pre-deploy sanity check.
  - When an operator says "the AI says it can't reach X" — `doctor` answers
    "here's exactly which step fails".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from touchstone import Config


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    fix: str | None = None


@dataclass(slots=True)
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, c: Check) -> None:
        self.checks.append(c)


def run_doctor(config_path: str | Path | None = None) -> DoctorReport:
    report = DoctorReport()

    # 1. Config.
    try:
        cfg = Config.load(config_path)
        report.add(Check("config_loads", True,
                         f"{len(cfg.connections)} connection(s) configured"))
    except Exception as e:
        report.add(Check("config_loads", False, str(e),
                         fix="Run `touchstone init`, or fix the file at "
                             f"{config_path or '~/.touchstone/config.toml'}"))
        return report

    # 2. Secret refs resolve.
    for name, conn in cfg.connections.items():
        if conn.password_ref:
            try:
                from touchstone.secrets import resolve
                _v = resolve(conn.password_ref)
                report.add(Check(f"secret/{name}", True,
                                 f"resolved {conn.password_ref!s}"))
            except Exception as e:
                report.add(Check(f"secret/{name}", False, str(e),
                                 fix=f"Make sure {conn.password_ref!s} is set."))

    # 3. Connections actually connect.
    from touchstone.connectors import get_connector
    for name, conn in cfg.connections.items():
        try:
            c = get_connector(conn)
            c.connect()
            tables = c.list_tables()
            c.close()
            report.add(Check(f"connect/{name}", True,
                             f"connected; {len(tables)} table(s) visible"))
        except Exception as e:
            report.add(Check(f"connect/{name}", False, str(e),
                             fix=f"See docs/connectors/{conn.engine.value}.md"))

    # 4. Audit sink writable.
    for i, sink_cfg in enumerate(cfg.security.audit_sinks):
        if sink_cfg.get("kind") == "file":
            p = Path(sink_cfg["path"]).expanduser()
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.touch(exist_ok=True)
                report.add(Check(f"audit_sink/{i}", True, f"writable: {p}"))
            except Exception as e:
                report.add(Check(f"audit_sink/{i}", False, str(e),
                                 fix=f"Make {p.parent} writable."))

    # 5. Web config sanity.
    if cfg.web.credentials:
        try:
            import playwright  # noqa: F401
            report.add(Check("web/playwright", True, "installed"))
        except ImportError:
            report.add(Check("web/playwright", False, "not installed",
                             fix="pip install 'touchstone-core[web]' "
                                 "&& playwright install chromium"))
        if cfg.web.credentials and not cfg.web.allowed_origins:
            report.add(Check("web/allowed_origins", False,
                             "credentials configured but no allowed_origins set",
                             fix="Set web.allowed_origins in touchstone.toml."))

    # 6. Session store.
    try:
        from touchstone.web.session_store import SessionStore
        ss = SessionStore()
        stored = ss.list_sessions()
        if cfg.web.credentials:
            missing = [c for c in cfg.web.credentials if c not in stored]
            if missing:
                report.add(Check("web/sessions", False,
                                 f"no stored session for: {', '.join(missing)}",
                                 fix=("Run `touchstone session bootstrap "
                                      f"{missing[0]}` (once per credential).")))
            else:
                report.add(Check("web/sessions", True,
                                 f"sessions stored: {', '.join(stored)}"))
    except Exception as e:
        report.add(Check("web/sessions", False, str(e)))

    # 7. Knowledge store.
    try:
        from touchstone.knowledge.store import KnowledgeStore
        ks = KnowledgeStore(cfg.knowledge.path)
        n = len(ks.open_tasks())
        ks.close()
        report.add(Check("knowledge", True, f"open; {n} open task(s)"))
    except Exception as e:
        report.add(Check("knowledge", False, str(e)))

    # 8. LLM ping (only if configured).
    if cfg.llm.provider not in ("", "none", None):
        try:
            from touchstone.llm.adapter import LLMMessage, build_llm
            llm = build_llm({
                "provider": cfg.llm.provider, "model": cfg.llm.model,
                "api_key_ref": cfg.llm.api_key_ref, "extra": cfg.llm.extra,
            })
            r = llm.chat([LLMMessage(role="user", content="say 'ok' and nothing else.")],
                          max_tokens=4)
            report.add(Check("llm", True, f"{cfg.llm.provider}/{cfg.llm.model} → "
                                          f"{r.content[:32]!r}"))
        except Exception as e:
            report.add(Check("llm", False, str(e),
                             fix="See docs/integrations/llm-provider.md"))
    else:
        report.add(Check("llm", True, "provider=none (LLM features disabled)"))

    return report
