#!/usr/bin/env bash
# Validação local: compilação Python, testes de API em BD temporário, build do frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== [1/5] Python compileall (backend/app) =="
python -m compileall -q backend/app

echo "== [2/5] Backend — tools/check_functionality.py =="
python tools/check_functionality.py

echo "== [3/5] Backend — pytest (CORS preflight, etc.) =="
( cd backend && pytest tests -q )

echo "== [4/5] BGP Advertised-to — tools/simulate_bgp_advertised_parse.py =="
python tools/simulate_bgp_advertised_parse.py

echo "== [5/5] Frontend — npm run build =="
npm run build

echo ""
echo "check-local: tudo OK."
