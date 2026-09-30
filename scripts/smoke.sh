#!/usr/bin/env bash
# Live end-to-end check. Needs ANTHROPIC_API_KEY.
set -euo pipefail

uv run python scripts/make_sample.py samples

echo "--- clean document, single pass ---"
uv run co-extract samples/sample_clean.pdf --out out/clean.json

echo "--- flawed document, expect ROW_EXTENDED_MISMATCH + SUBTOTAL_MISMATCH ---"
uv run co-extract samples/sample_flawed.pdf --out out/flawed.json

echo "--- scanned document, expect vision mode ---"
uv run co-extract samples/sample_scanned.pdf --out out/scanned.json

echo "--- flawed document, two passes with cross-check ---"
uv run co-extract samples/sample_flawed.pdf --verify --out out/flawed_verified.json

echo "smoke ok"
