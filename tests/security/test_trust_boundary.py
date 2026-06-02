"""Trust-boundary tests.

These tests assert that no QA capability can reach a connector except through
the Gateway. If you find yourself "fixing" one of these by adding the QA
package to the allowlist, stop and reconsider — that's the failure mode
they're here to catch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[2] / "packages" / "touchstone-core" / "src" / "touchstone"
ALLOWED_TO_IMPORT_CONNECTORS = {
    "touchstone.security.gateway",
    "touchstone.security.__init__",
    "touchstone.connectors",
}


def _module_name(path: Path) -> str:
    rel = path.relative_to(PKG_ROOT.parent).with_suffix("")
    return ".".join(rel.parts)


@pytest.mark.security
def test_no_qa_module_imports_connectors_directly():
    offenders = []
    for py in (PKG_ROOT / "qa").rglob("*.py"):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("touchstone.connectors"):
                    offenders.append(f"{py.relative_to(PKG_ROOT)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for n in node.names:
                    if n.name.startswith("touchstone.connectors"):
                        offenders.append(f"{py.relative_to(PKG_ROOT)}:{node.lineno}")
    assert not offenders, (
        "QA modules must not import from touchstone.connectors directly. "
        "Use the security.gateway. Offenders: " + ", ".join(offenders)
    )


@pytest.mark.security
def test_mcp_and_cli_do_not_import_connectors():
    pkgs_root = PKG_ROOT.parent.parent.parent
    offenders = []
    for surface in ("touchstone-mcp", "touchstone-cli"):
        for py in (pkgs_root / surface / "src").rglob("*.py"):
            tree = ast.parse(py.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("touchstone.connectors"):
                        offenders.append(f"{py}:{node.lineno}")
    assert not offenders, (
        "Surfaces must not import from touchstone.connectors directly. "
        f"Offenders: {offenders}"
    )
