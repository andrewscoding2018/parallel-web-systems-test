#!/usr/bin/env bash
# Convenience launcher: starts the FastAPI backend and the Vite frontend together.
# Backend on :8000, frontend on :5173 (proxies /api -> :8000).
set -euo pipefail
cd "$(dirname "$0")"

if [[ -z "${PARALLEL_API_KEY:-}" ]]; then
  echo "PARALLEL_API_KEY is not set. Add it to backend/.env or export it." >&2
fi

# Backend
(
  cd backend
  [[ -d .venv ]] || python3 -m venv .venv
  . .venv/bin/activate
  pip install -q -r requirements.txt
  uvicorn app.main:app --reload --port 8000
) &
BACKEND_PID=$!

# Frontend
(
  cd frontend
  [[ -d node_modules ]] || npm install
  npm run dev
) &
FRONTEND_PID=$!

trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' EXIT INT TERM
wait
