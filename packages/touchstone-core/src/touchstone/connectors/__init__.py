"""Database connectors.

CRITICAL: This package may ONLY be imported by:
  - touchstone.security.gateway (the single legitimate entry point)
  - touchstone.connectors.* internally
  - tests in tests/

Importing from touchstone.qa, touchstone.mcp_server, or anywhere else BYPASSES
the trust boundary. A Ruff custom rule and an integration test enforce this.
"""

from touchstone.connectors.base import Connector
from touchstone.connectors.registry import REGISTRY, get_connector

__all__ = ["Connector", "REGISTRY", "get_connector"]
