"""Matching + scoring — the transparent core of the AI Visibility Index.

Everything here is pure (no I/O) so the logic can be unit-tested against
fixture search results without touching the network.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from .models import CompetitorHit, QueryResult, ScoreBreakdown

# Common corporate suffixes we strip when deriving a matchable name token.
_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "co", "company", "gmbh", "ag", "sa", "plc", "technologies", "technology",
    "tech", "group", "industries", "international", "manufacturing", "mfg",
}


def normalize(text: str) -> str:
    """Lowercase and collapse to alphanumeric tokens separated by single spaces."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def registrable_domain(domain_or_url: str) -> str:
    """Reduce a domain or URL to a bare host without scheme, www, or path.

    Not a full public-suffix parse — good enough to compare hosts for the demo.
    """
    s = domain_or_url.strip().lower()
    if "://" not in s:
        s = "http://" + s
    host = urlparse(s).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host


def company_name_tokens(name: str) -> list[str]:
    """Significant tokens of a company name, with corporate suffixes removed.

    "Torrey Hills Technologies" -> ["torrey", "hills"].
    Falls back to all tokens if stripping suffixes leaves nothing.
    """
    tokens = normalize(name).split()
    significant = [t for t in tokens if t not in _SUFFIXES and len(t) > 1]
    return significant or tokens


def derive_company_name(domain: str) -> str:
    """Best-effort human name from a domain when the caller doesn't supply one."""
    host = registrable_domain(domain)
    label = host.split(".")[0] if host else domain
    return label.replace("-", " ").title()


def parse_competitor(raw: str) -> tuple[str, str | None]:
    """Split a competitor string into (name, domain).

    Accepts forms like "Acme Furnaces (acme.com)", "Acme - acme.com",
    or a bare domain "acme.com".
    """
    raw = raw.strip()
    # Domain in parentheses: "Name (domain.com)"
    m = re.search(r"\(([^)]*\.[^)]+)\)", raw)
    if m:
        name = raw[: m.start()].strip(" -–—")
        return (name or raw, registrable_domain(m.group(1)))
    # A bare domain with no spaces.
    if " " not in raw and "." in raw:
        return (raw, registrable_domain(raw))
    # Trailing "name - domain.com" or "name, domain.com".
    m = re.search(r"[\s,\-–—]+([a-z0-9-]+\.[a-z.]{2,})\s*$", raw, re.IGNORECASE)
    if m:
        name = raw[: m.start()].strip(" -–—,")
        return (name or raw, registrable_domain(m.group(1)))
    return (raw, None)


def _mentions(name_tokens: list[str], haystack: str) -> bool:
    """True if every significant token of the name appears in the normalized text.

    Requiring all tokens avoids matching "Hills" alone for "Torrey Hills".
    """
    if not name_tokens:
        return False
    h = normalize(haystack)
    return all(re.search(rf"\b{re.escape(tok)}\b", h) is not None for tok in name_tokens)


def evaluate_query(
    query: str,
    results: list[dict],
    company_domain: str,
    company_name: str,
    competitors: list[str],
) -> QueryResult:
    """Inspect one query's search results for company + competitor presence."""
    company_host = registrable_domain(company_domain)
    name_tokens = company_name_tokens(company_name)
    parsed_competitors = [(*parse_competitor(c),) for c in competitors]

    own_domain_present = False
    name_mentioned = False
    rank: int | None = None
    competitor_first_rank: dict[str, int] = {}

    for i, r in enumerate(results):
        url = r.get("url", "") or ""
        title = r.get("title", "") or ""
        excerpts = r.get("excerpts", []) or []
        blob = " ".join([title, *excerpts])
        result_host = registrable_domain(url)

        # (a) own-domain present
        if company_host and (result_host == company_host or result_host.endswith("." + company_host)):
            own_domain_present = True
            if rank is None:
                rank = i

        # (b) name mention anywhere in title/excerpt
        if _mentions(name_tokens, blob):
            name_mentioned = True
            if rank is None:
                rank = i

        # competitors — match by domain (on url) or by name (anywhere)
        for cname, cdomain in parsed_competitors:
            if cname in competitor_first_rank:
                continue
            hit = False
            if cdomain and (result_host == cdomain or result_host.endswith("." + cdomain)):
                hit = True
            elif _mentions(company_name_tokens(cname), blob):
                hit = True
            if hit:
                competitor_first_rank[cname] = i

    appeared = own_domain_present or name_mentioned
    competitors_appeared = [
        CompetitorHit(name=n, rank=r)
        for n, r in sorted(competitor_first_rank.items(), key=lambda kv: kv[1])
    ]

    return QueryResult(
        query=query,
        own_domain_present=own_domain_present,
        name_mentioned=name_mentioned,
        appeared=appeared,
        rank=rank,
        competitors_appeared=competitors_appeared,
        result_count=len(results),
    )


def score(per_query: list[QueryResult], results_per_query: int) -> ScoreBreakdown:
    """Blend the three component scores into the 0-100 index.

    index = round(
        0.5 * presence_rate    * 100 +
        0.3 * share_of_voice   * 100 +
        0.2 * rank_quality     * 100
    )
    """
    n = len(per_query)
    if n == 0:
        return ScoreBreakdown(
            presence_rate=0.0, share_of_voice=0.0, rank_quality=0.0, avg_rank=None, index=0
        )

    appearances = sum(1 for q in per_query if q.appeared)
    presence_rate = appearances / n

    company_appearances = appearances
    competitor_appearances = sum(len(q.competitors_appeared) for q in per_query)
    denom = company_appearances + competitor_appearances
    share_of_voice = (company_appearances / denom) if denom else 0.0

    ranks = [q.rank for q in per_query if q.rank is not None]
    avg_rank = (sum(ranks) / len(ranks)) if ranks else None
    denom_rank = max(results_per_query, 1)
    # rank_quality only counts when present; absent queries contribute 0 via presence.
    rank_quality = (1 - (avg_rank / denom_rank)) if avg_rank is not None else 0.0
    rank_quality = max(0.0, min(1.0, rank_quality))

    index = round(
        0.5 * presence_rate * 100
        + 0.3 * share_of_voice * 100
        + 0.2 * rank_quality * 100
    )

    return ScoreBreakdown(
        presence_rate=presence_rate,
        share_of_voice=share_of_voice,
        rank_quality=rank_quality,
        avg_rank=avg_rank,
        index=index,
    )


def find_blind_spots(per_query: list[QueryResult]) -> list[QueryResult]:
    """Queries where the company is absent but at least one competitor appears."""
    return [q for q in per_query if not q.appeared and q.competitors_appeared]
