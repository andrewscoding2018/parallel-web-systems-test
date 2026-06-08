"""Pydantic request/response models shared across the API."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Stage 1 — Deep dive (Task API)
# ---------------------------------------------------------------------------
class ProfileRequest(BaseModel):
    domain: str = Field(..., description="Company domain, e.g. 'torreyhillstech.com'.")


class CompanyProfile(BaseModel):
    one_liner: str | None = None
    product_lines: list[str] = Field(default_factory=list)
    buyer_queries: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)


class ProfileResponse(BaseModel):
    domain: str
    profile: CompanyProfile
    # Parallel "basis" (citations / reasoning / confidence) for the profile, if present.
    basis: list[dict] | None = None


# ---------------------------------------------------------------------------
# Stage 2 — Topical comparison (Search API + scoring)
# ---------------------------------------------------------------------------
class ScanRequest(BaseModel):
    domain: str
    company_name: str | None = Field(
        default=None,
        description="Company name for third-party mention matching. Derived from the domain if omitted.",
    )
    buyer_queries: list[str]
    competitors: list[str] = Field(default_factory=list)


class CompetitorHit(BaseModel):
    name: str
    rank: int


class QueryResult(BaseModel):
    query: str
    own_domain_present: bool
    name_mentioned: bool
    appeared: bool
    rank: int | None
    competitors_appeared: list[CompetitorHit]
    result_count: int


class ScoreBreakdown(BaseModel):
    presence_rate: float
    share_of_voice: float
    rank_quality: float
    avg_rank: float | None
    index: int


class ScanResponse(BaseModel):
    company: str
    domain: str
    per_query: list[QueryResult]
    score: ScoreBreakdown
    blind_spots: list[QueryResult]
