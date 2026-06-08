"""Unit tests for the matching + scoring core (no network)."""
from __future__ import annotations

from app.scoring import (
    company_name_tokens,
    derive_company_name,
    evaluate_query,
    find_blind_spots,
    parse_competitor,
    registrable_domain,
    score,
)

COMPANY_DOMAIN = "torreyhillstech.com"
COMPANY_NAME = "Torrey Hills Technologies"
COMPETITORS = ["Despatch (despatch.com)", "Centrotherm", "sierratherm.com"]


def test_registrable_domain_strips_scheme_www_and_path():
    assert registrable_domain("https://www.torreyhillstech.com/products") == "torreyhillstech.com"
    assert registrable_domain("beltfurnaces.com") == "beltfurnaces.com"


def test_company_name_tokens_drops_suffixes():
    assert company_name_tokens("Torrey Hills Technologies") == ["torrey", "hills"]
    # All-suffix name falls back to raw tokens rather than empty.
    assert company_name_tokens("Technologies Inc") != []


def test_derive_company_name_from_domain():
    assert derive_company_name("torreyhillstech.com") == "Torreyhillstech"
    assert derive_company_name("https://belt-furnaces.com") == "Belt Furnaces"


def test_parse_competitor_variants():
    assert parse_competitor("Despatch (despatch.com)") == ("Despatch", "despatch.com")
    assert parse_competitor("sierratherm.com") == ("sierratherm.com", "sierratherm.com")
    assert parse_competitor("Centrotherm") == ("Centrotherm", None)
    assert parse_competitor("BTU International - btu.com") == ("BTU International", "btu.com")


def _r(url="", title="", excerpts=None):
    return {"url": url, "title": title, "excerpts": excerpts or []}


def test_own_domain_present_detected():
    results = [
        _r(url="https://example.com/a", title="Some roundup"),
        _r(url="https://www.torreyhillstech.com/furnaces", title="Belt Furnaces"),
    ]
    qr = evaluate_query("belt furnace", results, COMPANY_DOMAIN, COMPANY_NAME, COMPETITORS)
    assert qr.own_domain_present is True
    assert qr.appeared is True
    assert qr.rank == 1


def test_name_mention_on_third_party_page():
    results = [
        _r(
            url="https://thomasnet.com/best-belt-furnaces",
            title="Best belt furnace makers",
            excerpts=["Top picks include Torrey Hills Technologies and others."],
        ),
    ]
    qr = evaluate_query("belt furnace", results, COMPANY_DOMAIN, COMPANY_NAME, COMPETITORS)
    assert qr.own_domain_present is False
    assert qr.name_mentioned is True
    assert qr.appeared is True
    assert qr.rank == 0


def test_partial_name_does_not_match():
    # "Hills" alone should not count as a Torrey Hills mention.
    results = [_r(url="https://x.com", title="Beverly Hills supplier directory")]
    qr = evaluate_query("supplier", results, COMPANY_DOMAIN, COMPANY_NAME, COMPETITORS)
    assert qr.name_mentioned is False
    assert qr.appeared is False


def test_competitor_detection_by_domain_and_name():
    results = [
        _r(url="https://despatch.com/products", title="Despatch furnaces"),
        _r(url="https://blog.com", title="Centrotherm review", excerpts=["centrotherm is great"]),
    ]
    qr = evaluate_query("furnace", results, COMPANY_DOMAIN, COMPANY_NAME, COMPETITORS)
    names = {c.name for c in qr.competitors_appeared}
    assert "Despatch" in names
    assert "Centrotherm" in names


def test_blind_spot_is_absent_company_with_present_competitor():
    results = [_r(url="https://despatch.com", title="Despatch")]
    qr = evaluate_query("furnace", results, COMPANY_DOMAIN, COMPANY_NAME, COMPETITORS)
    assert find_blind_spots([qr]) == [qr]


def test_score_blends_components():
    present = evaluate_query(
        "q1",
        [_r(url="https://torreyhillstech.com", title="THT")],
        COMPANY_DOMAIN,
        COMPANY_NAME,
        COMPETITORS,
    )
    absent = evaluate_query(
        "q2",
        [_r(url="https://despatch.com", title="Despatch")],
        COMPANY_DOMAIN,
        COMPANY_NAME,
        COMPETITORS,
    )
    s = score([present, absent], results_per_query=10)
    assert s.presence_rate == 0.5
    # company appeared once, competitor (despatch) appeared once -> 50% SoV
    assert s.share_of_voice == 0.5
    assert 0 <= s.index <= 100
    # present at rank 0 -> rank_quality 1.0; 0.5*0.5*100 + 0.3*0.5*100 + 0.2*1*100 = 25+15+20
    assert s.index == 60


def test_score_empty_is_zero():
    s = score([], results_per_query=10)
    assert s.index == 0
    assert s.avg_rank is None
