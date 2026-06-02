#!/usr/bin/env bash
#
# Touchstone one-line installer.
#
# Usage:
#     curl -fsSL https://raw.githubusercontent.com/VibhavSetlur/Touchstone/main/install.sh | bash
#
# Or inside a clone:
#     ./install.sh
#
# What it does:
#   1. Verifies Python >= 3.11.
#   2. Picks `uv` if available, falls back to `pip` in a venv.
#   3. Installs touchstone-core, touchstone-mcp, touchstone-cli with the
#      `quickstart` extras (Postgres + DuckDB + web automation).
#   4. Verifies the install with `touchstone --version` and `touchstone doctor
#      --non-interactive` (best-effort; fails are reported, not fatal).
#
# Environment overrides:
#   TOUCHSTONE_EXTRAS  — extras bundle, default "quickstart". Use "all" for
#                        every connector + every LLM provider.
#   TOUCHSTONE_VENV    — venv path, default ~/.touchstone-venv. Set to ""
#                        to install into the active Python (only with uv).
#   TOUCHSTONE_REF     — git ref to install from, default "main".

set -euo pipefail

REF="${TOUCHSTONE_REF:-main}"
EXTRAS="${TOUCHSTONE_EXTRAS:-quickstart}"
VENV="${TOUCHSTONE_VENV:-$HOME/.touchstone-venv}"
REPO="https://github.com/VibhavSetlur/Touchstone.git"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
warn() { printf "\033[33mwarn:\033[0m %s\n" "$*" >&2; }
err()  { printf "\033[31merror:\033[0m %s\n" "$*" >&2; }

bold "Touchstone installer"
echo

# ---------- 1. Python version check ----------
if ! command -v python3 >/dev/null 2>&1; then
    err "python3 not found. Install Python 3.11+ first."
    exit 1
fi
PYVER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYMAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PYMINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [[ "$PYMAJOR" -lt 3 ]] || { [[ "$PYMAJOR" -eq 3 ]] && [[ "$PYMINOR" -lt 11 ]]; }; then
    err "Python ${PYVER} is too old. Touchstone needs >= 3.11."
    exit 1
fi
echo "  Python ${PYVER}: ok"

# ---------- 2. Pick installer ----------
if command -v uv >/dev/null 2>&1; then
    bold "Using uv"
    INSTALLER="uv"
elif command -v pipx >/dev/null 2>&1; then
    bold "Using pipx + venv"
    INSTALLER="pipx"
else
    bold "Using pip + venv"
    INSTALLER="pip"
fi

# ---------- 3. Get the source ----------
if [[ -f "pyproject.toml" ]] && [[ -d "packages/touchstone-core" ]]; then
    SRC="$(pwd)"
    echo "  source: $(pwd) (existing clone)"
else
    SRC="$(mktemp -d)/touchstone"
    echo "  fetching $REF from $REPO"
    git clone --depth 1 --branch "$REF" "$REPO" "$SRC" >/dev/null 2>&1
fi

# ---------- 4. Install ----------
bold "Installing"

install_with_uv() {
    cd "$SRC"
    if [[ -n "$VENV" ]]; then
        uv venv "$VENV" --python "$(command -v python3)" >/dev/null
        # Activate it for subsequent commands.
        # shellcheck source=/dev/null
        source "$VENV/bin/activate"
    fi
    uv pip install -e "packages/touchstone-core[$EXTRAS]" >/dev/null
    uv pip install -e "packages/touchstone-mcp" >/dev/null
    uv pip install -e "packages/touchstone-cli" >/dev/null
}

install_with_pipx() {
    # pipx installs each console-script app into an isolated venv.
    # We use --editable + --pip-args to wire the local sibling packages.
    cd "$SRC"
    pipx install --force --editable "packages/touchstone-cli" \
                 --pip-args "-e packages/touchstone-core[$EXTRAS] -e packages/touchstone-mcp" \
                 2>/dev/null || {
        warn "pipx --pip-args failed (older pipx?); falling back to pip+venv"
        install_with_pip
        return
    }
}

install_with_pip() {
    cd "$SRC"
    if [[ -n "$VENV" ]]; then
        python3 -m venv "$VENV"
        # shellcheck source=/dev/null
        source "$VENV/bin/activate"
    fi
    pip install --upgrade pip >/dev/null
    pip install -e "packages/touchstone-core[$EXTRAS]" >/dev/null
    pip install -e "packages/touchstone-mcp" >/dev/null
    pip install -e "packages/touchstone-cli" >/dev/null
}

case "$INSTALLER" in
    uv) install_with_uv ;;
    pipx) install_with_pipx ;;
    pip) install_with_pip ;;
esac

# ---------- 5. Verify ----------
bold "Verifying"
if command -v touchstone >/dev/null 2>&1; then
    touchstone --version
else
    if [[ -n "$VENV" ]] && [[ -x "$VENV/bin/touchstone" ]]; then
        "$VENV/bin/touchstone" --version
        echo
        warn "touchstone is installed at $VENV/bin/touchstone but not on PATH."
        echo "       add: export PATH=\"$VENV/bin:\$PATH\""
    else
        err "touchstone command not found after install"
        exit 1
    fi
fi

echo
bold "Next steps"
cat <<EOF
  1. Initialize a config:        touchstone init
  2. Diagnose your setup:        touchstone doctor
  3. Try a profile:              touchstone profile <conn> <table>
  4. Wire into your AI editor:   see docs/integrations/

  Full docs: $REPO
EOF
