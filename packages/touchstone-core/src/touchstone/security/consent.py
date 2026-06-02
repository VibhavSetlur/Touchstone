"""Consent gate.

When a policy returns CONSENT_REQUIRED, we block the call and ask the operator.
Routing is pluggable — terminal (default), Slack, GitHub PR comment, or any
HTTP webhook.

The consent gate is the safety valve for "I can't enumerate every dangerous
thing in policy, but I can ensure a human approves anything that smells off."
"""

from __future__ import annotations

import json
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class ConsentRequest:
    assistant_id: str
    connection: str
    tool: str
    sql: str
    reason: str


class ConsentChannel(ABC):
    @abstractmethod
    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        ...


class TerminalChannel(ConsentChannel):
    """Prompt at the controlling terminal. Useful for local-dev MCP installs.

    Note: if stdin is not a TTY (e.g. running as a daemon), this auto-denies
    rather than block forever. Operators running headless should configure
    a Slack or webhook channel.
    """

    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        if not sys.stdin.isatty():
            return False
        print(
            f"\n[TOUCHSTONE CONSENT]\n"
            f"  Assistant : {req.assistant_id}\n"
            f"  Tool      : {req.tool}\n"
            f"  Connection: {req.connection}\n"
            f"  Reason    : {req.reason}\n"
            f"  SQL       : {req.sql[:300]}{'…' if len(req.sql) > 300 else ''}\n"
            f"  Approve? [y/N] (timeout {timeout_seconds}s) ",
            end="", flush=True, file=sys.stderr,
        )
        # Best-effort timeout — full timeout requires the consent gate to be
        # async, which is a roadmap item. For now, this is good enough for
        # interactive use.
        try:
            answer = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer == "y"


class WebhookChannel(ConsentChannel):
    """POST the request to a webhook and poll the returned URL for a verdict.

    The webhook contract:
      Request:  POST {webhook} JSON {assistant, tool, connection, sql, reason}
      Response: JSON {poll_url: "..."} OR JSON {decision: "approve"/"deny"}
      Poll:     GET {poll_url} → 202 (pending) | 200 JSON {decision: "..."}
    """

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        import urllib.error
        import urllib.request

        payload = json.dumps({
            "assistant_id": req.assistant_id,
            "tool": req.tool,
            "connection": req.connection,
            "sql": req.sql,
            "reason": req.reason,
            "timeout_seconds": timeout_seconds,
        }).encode("utf-8")
        try:
            resp = urllib.request.urlopen(  # noqa: S310 — operator-controlled URL
                urllib.request.Request(
                    self.webhook_url, data=payload,
                    headers={"Content-Type": "application/json"},
                ),
                timeout=10,
            )
            body = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError):
            return False

        if "decision" in body:
            return body["decision"] == "approve"

        poll_url = body.get("poll_url")
        if not poll_url:
            return False

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                r = urllib.request.urlopen(poll_url, timeout=10)  # noqa: S310
                if r.status == 200:
                    body = json.loads(r.read())
                    return body.get("decision") == "approve"
            except urllib.error.URLError:
                pass
            time.sleep(2)
        return False


class SlackChannel(ConsentChannel):
    """Post a Block Kit interactive message to a Slack channel; the user's
    button click flips a flag the gate polls. Implementation deferred — the
    Touchstone Slack app spec lives in docs/integrations/slack.md."""

    def __init__(self, webhook_url: str, channel_id: str) -> None:
        self.webhook_url = webhook_url
        self.channel_id = channel_id

    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        # Falls back to WebhookChannel semantics for now; the Slack-native
        # implementation is a roadmap item.
        return WebhookChannel(self.webhook_url).ask(req, timeout_seconds)


class AlwaysApproveChannel(ConsentChannel):
    """For tests and well-understood automation only."""

    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        return True


class AlwaysDenyChannel(ConsentChannel):
    """For belt-and-suspenders deployments where you want to be sure no
    consent-required op ever runs."""

    def ask(self, req: ConsentRequest, timeout_seconds: int) -> bool:
        return False


class ConsentGate:
    def __init__(self, channel: ConsentChannel | None = None) -> None:
        self.channel = channel or TerminalChannel()

    def request(
        self,
        assistant_id: str,
        connection: str,
        tool: str,
        sql: str,
        reason: str,
        timeout_seconds: int,
    ) -> bool:
        return self.channel.ask(
            ConsentRequest(assistant_id=assistant_id, connection=connection,
                           tool=tool, sql=sql, reason=reason),
            timeout_seconds=timeout_seconds,
        )
