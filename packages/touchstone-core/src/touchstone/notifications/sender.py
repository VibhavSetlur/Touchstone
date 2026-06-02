"""Notification sender — Slack, Teams, Email.

Webhook URLs are resolved server-side from the secret store. The AI never
sees them; it just passes a channel name.
"""

from __future__ import annotations

import json
import smtplib
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.mime.text import MIMEText
from typing import Any

from touchstone.secrets import resolve


@dataclass(slots=True)
class SendResult:
    ok: bool
    detail: str = ""


class Channel(ABC):
    @abstractmethod
    def send(self, message: str, *, subject: str | None = None,
             attachments: list[str] | None = None) -> SendResult:
        ...


class SlackChannel(Channel):
    def __init__(self, webhook_ref: str) -> None:
        self._webhook_ref = webhook_ref

    def send(self, message, *, subject=None, attachments=None):
        url = resolve(self._webhook_ref)
        payload = {"text": (f"*{subject}*\n{message}" if subject else message)}
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)  # noqa: S310 — operator-controlled
            return SendResult(ok=True)
        except Exception as e:
            return SendResult(ok=False, detail=str(e))


class TeamsChannel(Channel):
    """MS Teams incoming webhook (legacy Office 365 connector format)."""
    def __init__(self, webhook_ref: str) -> None:
        self._webhook_ref = webhook_ref

    def send(self, message, *, subject=None, attachments=None):
        url = resolve(self._webhook_ref)
        payload = {
            "@type": "MessageCard", "@context": "https://schema.org/extensions",
            "summary": subject or "Touchstone notification",
            "title": subject or "Touchstone",
            "text": message,
        }
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)  # noqa: S310
            return SendResult(ok=True)
        except Exception as e:
            return SendResult(ok=False, detail=str(e))


class EmailChannel(Channel):
    def __init__(
        self, *, to_ref: str, smtp_host: str, smtp_port: int = 587,
        smtp_user_ref: str | None = None, smtp_pass_ref: str | None = None,
        from_addr: str = "touchstone@localhost",
    ) -> None:
        self._to_ref = to_ref
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user_ref = smtp_user_ref
        self._smtp_pass_ref = smtp_pass_ref
        self._from = from_addr

    def send(self, message, *, subject=None, attachments=None):
        to = resolve(self._to_ref)
        msg = MIMEText(message)
        msg["Subject"] = subject or "Touchstone notification"
        msg["From"] = self._from
        msg["To"] = to
        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15) as s:
                s.ehlo()
                s.starttls()
                if self._smtp_user_ref and self._smtp_pass_ref:
                    s.login(resolve(self._smtp_user_ref), resolve(self._smtp_pass_ref))
                s.sendmail(self._from, [a.strip() for a in to.split(",")], msg.as_string())
            return SendResult(ok=True)
        except Exception as e:
            return SendResult(ok=False, detail=str(e))


class WebhookChannelGeneric(Channel):
    """Free-form JSON POST. Use when you want to forward to a custom sink."""
    def __init__(self, webhook_ref: str, template: dict[str, Any] | None = None) -> None:
        self._webhook_ref = webhook_ref
        self._template = template or {"text": "{{message}}", "subject": "{{subject}}"}

    def send(self, message, *, subject=None, attachments=None):
        url = resolve(self._webhook_ref)
        body = {}
        for k, v in self._template.items():
            if isinstance(v, str):
                v = v.replace("{{message}}", message).replace("{{subject}}", subject or "")
            body[k] = v
        try:
            req = urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=10)  # noqa: S310
            return SendResult(ok=True)
        except Exception as e:
            return SendResult(ok=False, detail=str(e))


class Notifier:
    """Holds the operator's configured channels. The AI assistant references
    channels by name; the Notifier resolves and dispatches."""

    def __init__(self, channels: dict[str, Channel]) -> None:
        self._channels = channels

    @classmethod
    def from_config(cls, configs: dict[str, dict[str, Any]]) -> Notifier:
        channels: dict[str, Channel] = {}
        for name, c in configs.items():
            kind = c["kind"]
            if kind == "slack":
                channels[name] = SlackChannel(c["webhook_ref"])
            elif kind == "teams":
                channels[name] = TeamsChannel(c["webhook_ref"])
            elif kind == "email":
                channels[name] = EmailChannel(
                    to_ref=c["to_ref"],
                    smtp_host=c["smtp_host"],
                    smtp_port=c.get("smtp_port", 587),
                    smtp_user_ref=c.get("smtp_user_ref"),
                    smtp_pass_ref=c.get("smtp_pass_ref"),
                    from_addr=c.get("from_addr", "touchstone@localhost"),
                )
            elif kind == "webhook":
                channels[name] = WebhookChannelGeneric(
                    c["webhook_ref"], c.get("template"),
                )
            else:
                raise ValueError(f"unknown notification kind: {kind!r}")
        return cls(channels)

    def list_channels(self) -> list[str]:
        return sorted(self._channels.keys())

    def send(self, channel: str, message: str, *,
             subject: str | None = None, attachments: list[str] | None = None) -> SendResult:
        ch = self._channels.get(channel)
        if ch is None:
            return SendResult(ok=False, detail=f"unknown channel: {channel!r}")
        return ch.send(message, subject=subject, attachments=attachments)
