#!/usr/bin/env bash
# Runs the automated proof for every test case and writes a dated report to docs/evidence/.
set -euo pipefail
cd "$(dirname "$0")/../backend"
out="../docs/evidence/tc-report-$(date +%Y%m%d-%H%M%S).txt"
{
  echo "SensoCrypt v2 - test case evidence ($(date))"
  echo "commit: $(git rev-parse --short HEAD)"
  echo
  .venv/bin/python -m pytest tests -v -p no:warnings \
    -k "tc04 or tc05 or tc06 or tc07 or slightest or unrelated or still_phone or failure_ or real_face or illumination or screen or reflection or covered or weak_flash" 2>&1
} | tee "$out"
echo "saved: $out"
