# AI Visibility Index

**How visible is a company to AI assistants — and where is it invisible?**

Buyers increasingly ask an AI assistant instead of running a Google search. Those
assistants answer from web context retrieved by search APIs like
[Parallel](https://parallel.ai)'s — token-dense excerpts ranked for LLM relevance,
not human-clickable blue links. So if you run a company's category/buyer queries
through Parallel Search and the company doesn't surface, **the AI literally cannot
recommend it.** It's invisible in the AI-native buying funnel.

This tool measures that. Give it a company domain; it returns an **AI Visibility
Index (0–100)**, a per-query breakdown, and a prioritized **blind-spot list** —
queries where the company is absent but competitors appear.

Built on the Parallel Web Systems API as a demo. Test case: Torrey Hills
Technologies (`torreyhillstech.com`).

---

## How it works

Two stages, end-to-end from just a domain:

| Stage | What | Parallel API |
|-------|------|--------------|
| **1 — Deep dive** | Understand the company; auto-generate the buyer queries it should win and its competitors. | **Task API** (`/v1/tasks/runs` → `/result`) |
| **2 — Topical comparison** | Run those queries through search; measure where the company shows up vs. competitors. | **Search API** (`/v1/search`) |

### Scoring (transparent on purpose)

Three component scores blend into the index:

- **Presence rate** — share of queries where the company appeared at all.
- **Share of voice** — company appearances ÷ (company + competitor) appearances.
- **Rank quality** — `1 − (avg_rank / results_per_query)` when present.

```
index = round(0.5·presence_rate·100 + 0.3·share_of_voice·100 + 0.2·rank_quality·100)
```

### Two matching signals (the insight is in the gap)

For each query we track:

- **(a) own-domain present** — the company's domain in any result URL.
- **(b) name mention** — the company name in any title/excerpt, *anywhere* (third-party
  roundups, "best X manufacturers" listicles, directories).

Assistants lean on third-party pages, so a company strong on (a) but weak on (b) has
a content/PR gap, not just an SEO one.

---

## Architecture

```
backend/   FastAPI — proxies all Parallel calls so the API key stays server-side
  app/
    main.py             POST /api/profile (Stage 1), POST /api/scan (Stage 2)
    parallel_client.py  raw httpx client for the Task + Search REST endpoints
    scoring.py          pure matching + scoring (unit-tested, no I/O)
    models.py           pydantic request/response models
    config.py           env-driven settings
  tests/                pytest unit tests for scoring/matching

frontend/  React + Vite + TypeScript — single-page UI
  src/
    App.tsx             two-stage flow + loading states
    api.ts              fetch wrappers (talks only to /api, proxied to backend)
    components/         ScoreCard, QueryTable, BlindSpots, ProfilePanel
```

The browser never sees the API key: it calls the frontend's `/api/*`, Vite proxies
to FastAPI, and FastAPI adds the `x-api-key` header server-side.

---

## Setup

1. Get an API key at [platform.parallel.ai](https://platform.parallel.ai).
2. Configure it for the backend:
   ```bash
   cp backend/.env.example backend/.env
   # edit backend/.env and set PARALLEL_API_KEY=...
   ```
   (Or export `PARALLEL_API_KEY` in your shell.)

## Run

```bash
./dev.sh          # starts backend :8000 and frontend :5173 together
```

Then open http://localhost:5173.

Run them separately if you prefer:

```bash
# backend
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend (new terminal)
cd frontend && npm install && npm run dev
```

## Test

```bash
cd backend && . .venv/bin/activate && python -m pytest -q
```

---

## API reference

### `POST /api/profile`
```json
{ "domain": "torreyhillstech.com" }
```
Returns the structured profile (`one_liner`, `product_lines`, `buyer_queries`,
`competitors`) plus Parallel Basis (citations/reasoning) when available.

### `POST /api/scan`
```json
{
  "domain": "torreyhillstech.com",
  "buyer_queries": ["belt furnace for solar cell firing", "..."],
  "competitors": ["Despatch (despatch.com)", "..."]
}
```
Returns `per_query`, the `score` breakdown, and `blind_spots`.

---

## Notes & limitations

- Search has at times required a `parallel-beta` header; the current value lives in
  `backend/app/config.py` (`PARALLEL_SEARCH_BETA`) and is easy to override via env.
- Domain → host matching is a pragmatic comparison, not a full public-suffix parse.
- Out of scope for v1: written recommendations (a later Chat API call), multi-domain
  companies, saved scans / history / export, and auth.
