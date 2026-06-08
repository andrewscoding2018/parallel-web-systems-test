"""FastAPI entrypoint for the AI Visibility Index.

Two endpoints proxy all Parallel calls so the API key stays server-side:

  POST /api/profile  — Stage 1 deep dive (Task API)
  POST /api/scan     — Stage 2 topical comparison (Search loop + scoring)
"""
from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import (
    CompanyProfile,
    ProfileRequest,
    ProfileResponse,
    ScanRequest,
    ScanResponse,
)
from .parallel_client import ParallelClient, ParallelError
from .scoring import derive_company_name, evaluate_query, find_blind_spots, score

app = FastAPI(title="AI Visibility Index", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "api_key_configured": bool(settings.parallel_api_key)}


@app.post("/api/profile", response_model=ProfileResponse)
async def profile(req: ProfileRequest) -> ProfileResponse:
    """Stage 1 — generate the company profile, buyer queries, and competitors."""
    client = _client()
    try:
        result = await client.deep_dive(req.domain)
    except ParallelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    raw = result.get("profile") or {}
    return ProfileResponse(
        domain=req.domain,
        profile=CompanyProfile(
            company_name=raw.get("company_name"),
            one_liner=raw.get("one_liner"),
            product_lines=raw.get("product_lines", []),
            buyer_queries=raw.get("buyer_queries", []),
            competitors=raw.get("competitors", []),
        ),
        basis=result.get("basis"),
    )


@app.post("/api/scan", response_model=ScanResponse)
async def scan(req: ScanRequest) -> ScanResponse:
    """Stage 2 — run each buyer query through Search and score visibility."""
    if not req.buyer_queries:
        raise HTTPException(status_code=400, detail="buyer_queries must not be empty.")

    client = _client()
    company_name = req.company_name or derive_company_name(req.domain)

    async def run_one(query: str):
        objective = f"Find suppliers and manufacturers for: {query}"
        # Pair the query with a light paraphrase to broaden recall.
        search_queries = [query, f"{query} manufacturer supplier"]
        results = await client.search(objective, search_queries)
        return evaluate_query(
            query=query,
            results=results,
            company_domain=req.domain,
            company_name=company_name,
            competitors=req.competitors,
        )

    try:
        per_query = await asyncio.gather(*(run_one(q) for q in req.buyer_queries))
    except ParallelError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    per_query = list(per_query)
    breakdown = score(per_query, settings.results_per_query)
    blind_spots = find_blind_spots(per_query)

    return ScanResponse(
        company=company_name,
        domain=req.domain,
        per_query=per_query,
        score=breakdown,
        blind_spots=blind_spots,
    )


def _client() -> ParallelClient:
    try:
        return ParallelClient(settings)
    except ParallelError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
