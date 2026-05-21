#!/usr/bin/env bash
set -euo pipefail

GSTACK_DIR="$HOME/.claude/skills/gstack"

if [ -d "$GSTACK_DIR" ]; then
    echo "[ok] gstack already installed at $GSTACK_DIR"
    exit 0
fi

if ! command -v bun >/dev/null 2>&1; then
    echo "[info] installing bun..."
    BUN_VERSION="1.3.10"
    tmpfile=$(mktemp)
    curl -fsSL "https://bun.sh/install" -o "$tmpfile"
    BUN_VERSION="$BUN_VERSION" bash "$tmpfile"
    rm "$tmpfile"
    export PATH="$HOME/.bun/bin:$PATH"
fi

echo "[info] cloning gstack..."
git clone --single-branch --depth 1 https://github.com/garrytan/gstack.git "$GSTACK_DIR"
cd "$GSTACK_DIR" && ./setup

echo "[ok] gstack ready"
