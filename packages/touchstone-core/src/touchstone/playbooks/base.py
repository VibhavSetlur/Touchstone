"""Playbook base class + shared report shape."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PlaybookStep:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlaybookReport:
    playbook: str
    steps: list[PlaybookStep] = field(default_factory=list)
    summary: str = ""
    suggested_actions: list[str] = field(default_factory=list)
    next_tasks: list[dict[str, Any]] = field(default_factory=list)  # → Knowledge tasks

    @property
    def passed(self) -> bool:
        return all(s.ok for s in self.steps)

    def to_markdown(self) -> str:
        lines = [f"# Playbook: {self.playbook}", "", self.summary, ""]
        lines.append("## Steps")
        for s in self.steps:
            mark = "✅" if s.ok else "❌"
            lines.append(f"- {mark} **{s.name}** — {s.detail}")
        if self.suggested_actions:
            lines.append("\n## Suggested actions")
            for a in self.suggested_actions:
                lines.append(f"- {a}")
        if self.next_tasks:
            lines.append("\n## Next tasks")
            for t in self.next_tasks:
                lines.append(f"- {t.get('title', '')}")
        return "\n".join(lines)


class Playbook(ABC):
    """Playbook contract.

    Implementations get whatever services they need via constructor injection
    (gateway, knowledge store, GitHub API, browser session). They MUST NOT
    open new connectors directly — go through the gateway.
    """

    name: str = "playbook"

    @abstractmethod
    def run(self, **kwargs: Any) -> PlaybookReport:
        ...
