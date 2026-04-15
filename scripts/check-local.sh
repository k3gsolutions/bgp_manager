#!/usr/bin/env bash
# Validação local: compilação Python, testes de API em BD temporário, build do frontend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== [1/3] Python compileall (backend/app) =="
python -m compileall -q backend/app

echo "== [2/4] Backend — tools/check_functionality.py =="
python tools/check_functionality.py

echo "== [3/4] Backend — pytest (CORS preflight, etc.) =="
( cd backend && pytest tests -q )

echo "== [4/4] Frontend — npm run build =="
npm run build

echo ""
echo "check-local: tudo OK."
