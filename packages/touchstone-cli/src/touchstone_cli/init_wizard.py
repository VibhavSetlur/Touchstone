"""Interactive `touchstone init`.

Detects local DuckDB / Postgres / MySQL, asks the user about Snowflake / BQ,
writes ~/.touchstone/config.toml. The wizard never asks for plaintext passwords
— it asks for the secret reference (env://, keyring://, etc.) and explains
why.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import tomli_w


def run_init(non_interactive: bool = False) -> Path:
    target = Path(os.environ.get("TOUCHSTONE_CONFIG",
                                  Path.home() / ".touchstone" / "config.toml"))
    target.parent.mkdir(parents=True, exist_ok=True)

    config = {"connections": {}, "security": _default_security(), "assistants": {}}

    if non_interactive:
        config["connections"]["local-duckdb"] = {
            "engine": "duckdb",
            "database": str(Path.home() / ".touchstone" / "scratch.duckdb"),
            "tags": ["dev"],
            "read_only": False,
        }
        _write(target, config)
        return target

    import questionary

    # DuckDB default
    if questionary.confirm("Add a local DuckDB connection for ad-hoc QA?",
                           default=True).ask():
        path = questionary.text(
            "DuckDB file path:",
            default=str(Path.home() / ".touchstone" / "scratch.duckdb"),
        ).ask()
        config["connections"]["local-duckdb"] = {
            "engine": "duckdb", "database": path, "tags": ["dev"], "read_only": False,
        }

    # Postgres
    if _port_open("localhost", 5432) and questionary.confirm(
        "Detected Postgres on localhost:5432 — add it?", default=True
    ).ask():
        name = questionary.text("Connection name:", default="local-pg").ask()
        db = questionary.text("Database:", default="postgres").ask()
        user = questionary.text("User:", default="postgres").ask()
        pwd_ref = questionary.text(
            "Password reference (e.g. env://POSTGRES_PASSWORD):",
            default="env://POSTGRES_PASSWORD",
        ).ask()
        config["connections"][name] = {
            "engine": "postgres", "host": "localhost", "port": 5432,
            "database": db, "user": user, "password_ref": pwd_ref,
            "tags": ["dev"], "read_only": True,
        }

    # Snowflake (always asked, gated)
    if questionary.confirm("Add a Snowflake connection?", default=False).ask():
        name = questionary.text("Connection name:", default="snowflake-ro").ask()
        account = questionary.text("Snowflake account:").ask()
        user = questionary.text("User:").ask()
        warehouse = questionary.text("Warehouse:").ask()
        database = questionary.text("Database:").ask()
        schema = questionary.text("Schema:", default="PUBLIC").ask()
        pwd_ref = questionary.text(
            "Password reference (or key_pair private_key_file path under `extra.private_key_file`):",
            default="env://SNOWFLAKE_PASSWORD",
        ).ask()
        config["connections"][name] = {
            "engine": "snowflake", "user": user,
            "database": database, "schema": schema,
            "password_ref": pwd_ref,
            "extra": {"account": account, "warehouse": warehouse, "auth": "password"},
            "tags": ["prod"], "read_only": True,
        }

    _write(target, config)
    return target


def _write(target: Path, config: dict) -> None:
    with target.open("wb") as f:
        tomli_w.dump(config, f)


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _default_security() -> dict:
    return {
        "policy_files": [],
        "pii_threshold": 0.4,
        "pii_default_strategy": "redact",
        "pii_detectors_enabled": ["column_name", "regex"],
        "consent_required_on": [
            "non_select", "tagged:prod", "tagged:sensitive",
            "large_result", "new_connection_24h",
        ],
        "consent_timeout_seconds": 300,
        "rate_limit_per_minute": 60,
        "audit_sinks": [
            {"kind": "file", "path": str(Path.home() / ".touchstone" / "audit.jsonl")},
        ],
    }
