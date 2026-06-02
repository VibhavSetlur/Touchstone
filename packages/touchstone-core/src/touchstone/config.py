"""Configuration loading and validation.

Config can come from:
  - A TOML file (`touchstone init` writes one).
  - Environment variables (TOUCHSTONE_*).
  - Programmatic construction in tests.

Plaintext credentials in config files are rejected — fields must reference a
secret source (env://, keyring://, vault://, awssm://, gcpsm://, azurekv://).
This is enforced at load time, not at use time, so misconfiguration fails fast.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from touchstone.types import ConfigError, Engine


SECRET_SCHEMES = ("env://", "keyring://", "vault://", "awssm://", "gcpsm://", "azurekv://")


@dataclass(slots=True)
class ConnectionConfig:
    """One database connection.

    Credentials are NEVER stored here — only references via secret-scheme URIs.
    The connector resolves them at connect time and never logs them.
    """

    name: str
    engine: Engine
    host: str | None = None
    port: int | None = None
    database: str | None = None
    schema_: str | None = None  # `schema` collides with pydantic
    user: str | None = None
    password_ref: str | None = None  # secret-scheme URI
    extra: dict[str, Any] = field(default_factory=dict)

    read_only: bool = True
    tags: list[str] = field(default_factory=list)  # e.g. ["prod", "pii-strict"]
    row_cap: int = 10_000
    byte_cap: int = 32 * 1024 * 1024
    timeout_seconds: int = 30

    def validate(self) -> None:
        if self.password_ref is not None:
            if not any(self.password_ref.startswith(s) for s in SECRET_SCHEMES):
                raise ConfigError(
                    f"connection {self.name!r}: password_ref must use a secret scheme "
                    f"(one of {', '.join(SECRET_SCHEMES)}); plaintext is not allowed."
                )
        if self.row_cap <= 0 or self.byte_cap <= 0 or self.timeout_seconds <= 0:
            raise ConfigError(f"connection {self.name!r}: caps and timeout must be positive.")


@dataclass(slots=True)
class SecurityConfig:
    policy_files: list[Path] = field(default_factory=list)
    pii_threshold: float = 0.4
    pii_default_strategy: str = "redact"  # redact | hash | tokenize | partial | synthetic
    pii_detectors_enabled: list[str] = field(default_factory=lambda: ["column_name", "regex"])
    consent_required_on: list[str] = field(
        default_factory=lambda: [
            "non_select",
            "tagged:prod",
            "tagged:sensitive",
            "large_result",
            "new_connection_24h",
        ]
    )
    consent_timeout_seconds: int = 300
    rate_limit_per_minute: int = 60
    audit_sinks: list[dict[str, Any]] = field(
        default_factory=lambda: [{"kind": "file", "path": "~/.touchstone/audit.jsonl"}]
    )


@dataclass(slots=True)
class WebConfig:
    allowed_origins: list[str] = field(default_factory=list)
    headless: bool = True
    context_dir: str | None = None
    session_store_dir: str = "~/.touchstone/web-sessions"
    credentials: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass(slots=True)
class CostConfig:
    max_estimated_rows: int = 100_000_000
    max_estimated_bytes: int = 10 * 1024 ** 3
    refuse_cross_join: bool = True
    require_limit_on_large: bool = True
    auto_inject_limit: int = 10_000
    concurrent_cap_per_assistant: int = 4
    large_tables: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TenantConfig:
    tenants: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class LLMConfig:
    provider: str = "none"     # "none" disables LLM-assisted features
    model: str = ""
    api_key_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class KnowledgeConfig:
    path: str = "~/.touchstone/knowledge.db"


@dataclass(slots=True)
class NotificationsConfig:
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class Config:
    connections: dict[str, ConnectionConfig] = field(default_factory=dict)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    assistants: dict[str, list[str]] = field(default_factory=dict)
    web: WebConfig = field(default_factory=WebConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    knowledge: KnowledgeConfig = field(default_factory=KnowledgeConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    tenants: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-connection sensitivity catalogs: {connection_name: {tier: [cols]}}
    sensitivity: dict[str, dict[str, Any]] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.connections:
            raise ConfigError("no connections configured. Run `touchstone init` to create one.")
        for c in self.connections.values():
            c.validate()

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        path = Path(path) if path else _default_config_path()
        if not path.exists():
            raise ConfigError(f"config file not found: {path}. Run `touchstone init`.")
        with path.open("rb") as f:
            data = tomllib.load(f)
        cfg = _from_dict(data)
        cfg.validate()
        return cfg


def _from_dict(data: dict[str, Any]) -> Config:
    connections: dict[str, ConnectionConfig] = {}
    for name, raw in data.get("connections", {}).items():
        engine = Engine(raw.pop("engine"))
        # rename `schema` -> `schema_` for the dataclass
        if "schema" in raw:
            raw["schema_"] = raw.pop("schema")
        connections[name] = ConnectionConfig(name=name, engine=engine, **raw)

    sec_raw = data.get("security", {})
    if "policy_files" in sec_raw:
        sec_raw["policy_files"] = [Path(p).expanduser() for p in sec_raw["policy_files"]]
    security = SecurityConfig(**sec_raw)

    assistants = data.get("assistants", {})
    web = WebConfig(**data.get("web", {}))
    llm = LLMConfig(**data.get("llm", {}))
    knowledge = KnowledgeConfig(**data.get("knowledge", {}))
    notif_raw = data.get("notifications", {})
    notifications = NotificationsConfig(channels=notif_raw.get("channels", {}))

    cost = CostConfig(**{k: v for k, v in data.get("cost", {}).items()
                          if k in CostConfig.__dataclass_fields__})
    tenants = data.get("tenants", {}) or {}
    sensitivity = data.get("sensitivity", {}) or {}

    return Config(
        connections=connections, security=security, assistants=assistants,
        web=web, llm=llm, knowledge=knowledge, notifications=notifications,
        cost=cost, tenants=tenants, sensitivity=sensitivity,
    )


def _default_config_path() -> Path:
    override = os.environ.get("TOUCHSTONE_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path("~/.touchstone/config.toml").expanduser()
