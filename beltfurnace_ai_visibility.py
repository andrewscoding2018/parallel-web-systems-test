"""
AI Visibility Experiment — XMZ / Torrey Hills / HENGLI  (beltfurnaces.com)

Measures how well web-search-based AI retrieval actually "sees" and evaluates the
company, using Parallel Web Systems as the retrieval layer (a clean proxy for what
ChatGPT / Perplexity / Claude do under the hood).

Three experiments:
  1. Discoverability & share-of-voice        -> Parallel Search API
  2. Entity resolution (the 4-name problem)  -> Parallel Task API (structured)
  3. Reputation capture, old name vs new name-> Parallel Task API (deep research)

Plus (no API): a deterministic site-defect check of beltfurnaces.com itself
(NAP inconsistencies, title/meta typos, encoding errors, missing schema.org).

Setup:
    pip install parallel-web requests pydantic matplotlib beautifulsoup4
    export PARALLEL_API_KEY="your-key"

Run:
    python beltfurnace_ai_visibility.py smoke     # 1 Search + 1 Task call, prints raw JSON
    python beltfurnace_ai_visibility.py sweep     # all 3 experiments (resumable, incremental save)
    python beltfurnace_ai_visibility.py defects   # deterministic site checks (no API)
    python beltfurnace_ai_visibility.py analyze   # metrics_summary.json, findings.md, charts
    python beltfurnace_ai_visibility.py all       # sweep + defects + analyze

Outputs (out/):
    raw_results.json      every call, incl. every basis citation + confidence
    metrics_summary.json  deck-ready headline numbers only
    findings.md           numbered findings w/ evidence + severity (factual, no recs)
    defects.json          deterministic site defects
    charts/*.png          four charts

Every API call is persisted to out/raw_results.json the moment it completes, and
completed calls are skipped on re-run, so a crashed sweep resumes where it left off.
"""

import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------
OUT_DIR = Path(__file__).parent / "out"
CHARTS_DIR = OUT_DIR / "charts"
RAW_PATH = OUT_DIR / "raw_results.json"

SEARCH_URL = "https://api.parallel.ai/v1beta/search"
SEARCH_PROCESSOR = "pro"
ENTITY_PROCESSOR = "core"
REPUTATION_PROCESSOR = "pro"

TARGET_DOMAIN = "beltfurnaces.com"
# The company's own web properties (own-site for citation classification).
OWN_DOMAINS = {"beltfurnaces.com", "torreyhillstech.com", "ththeatsinks.com"}

IDENTITIES = {
    "XMZ (new brand)": ["xmz technologies", "xmz "],
    "Torrey Hills (old)": ["torrey hills"],
    "HENGLI (product brand)": ["hengli"],
}

# Named competing furnace manufacturers (domain match on result URLs / citations).
COMPETITOR_DOMAINS = [
    "btu.com", "despatch.com", "sierratherm.com", "cmfurnaces.com",
    "lindbergmph.com", "surfacecombustion.com", "jtektthermos.com",
    "heatshieldtechnologies.com", "pinnacletechnologies.com", "nutec-bickley.com",
    "carbolite-gero.com", "thermcraftinc.com", "sentrotech.com", "abbottfurnace.com",
]
# B2B directories / marketplaces (kept separate from competitors on purpose).
DIRECTORY_DOMAINS = [
    "made-in-china.com", "thomasnet.com", "alibaba.com", "globalspec.com",
    "indiamart.com", "tradeindia.com", "directindustry.com", "kompass.com",
    "yellowpages.com", "europages.com", "environmental-expert.com", "ec21.com",
]

# Proof points that SHOULD surface if retrieval truly "sees" their reputation.
PROOF_POINTS = [
    "NASA", "IBM", "Apple", "First Solar", "International Rectifier",
    "Golden Bridge", "ISO 9001", "CE marking", "115 million",
]

BUYER_QUERIES = [
    "conveyor belt furnace manufacturer for thick film firing",
    "belt furnace for solar cell metallization supplier",
    "atmosphere controlled belt furnace semiconductor packaging",
    "furnace for LTCC and hybrid IC firing",
    "reflow soldering conveyor furnace manufacturer",
    "pusher furnace supplier advanced ceramics",
    "fast fire firing furnace photovoltaic manufacturer",
    "used thick film belt furnace for sale",
]
BRANDED_QUERIES = [
    "XMZ Technologies belt furnace",
    "Torrey Hills belt furnace",
    "HENGLI belt furnace manufacturer",
]

RUNS_PER_QUERY = 5   # repeat every probe so run-to-run drift is measurable
TOP_N = 10
MAX_RETRIES = 4
BACKOFFS = [2, 4, 8, 16]

# Published rate card (USD per 1000 requests) — used ONLY for the estimated cost
# tally; nothing derived from these numbers goes into metrics.
PRICE_PER_1K = {
    ("search", "base"): 4.0,
    ("search", "pro"): 9.0,
    ("task", "lite"): 5.0,
    ("task", "base"): 10.0,
    ("task", "core"): 25.0,
    ("task", "pro"): 100.0,
}

ENTITY_PROBES = [
    "beltfurnaces.com",
    "HENGLI belt furnaces",
    "XMZ Technologies furnace company",
]
REPUTATION_PROBES = [
    "Torrey Hills Technologies belt furnaces",
    "XMZ Technologies belt furnaces",
]

# Pages for the deterministic defect check (homepage + same-domain nav links).
SITE_ROOT = "https://beltfurnaces.com"
MAX_SITE_PAGES = 14


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_api_key():
    key = os.environ.get("PARALLEL_API_KEY")
    if not key:
        sys.exit("FATAL: PARALLEL_API_KEY is not set. Export it and re-run.")
    return key


# --------------------------------------------------------------------------------------
# Persistent state: every completed call is saved immediately; re-runs skip done calls.
# --------------------------------------------------------------------------------------
class State:
    def __init__(self):
        OUT_DIR.mkdir(exist_ok=True)
        CHARTS_DIR.mkdir(exist_ok=True)
        if RAW_PATH.exists():
            self.data = json.loads(RAW_PATH.read_text())
        else:
            self.data = {"calls": {}, "meta": {"created": now_iso()}}

    def has(self, call_id):
        return call_id in self.data["calls"] and self.data["calls"][call_id].get("ok")

    def record(self, call_id, payload):
        self.data["calls"][call_id] = payload
        self.data["meta"]["updated"] = now_iso()
        tmp = RAW_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, default=str))
        tmp.replace(RAW_PATH)

    def calls(self, prefix):
        return {k: v for k, v in self.data["calls"].items() if k.startswith(prefix)}


def with_retries(fn, label):
    """Run fn() with exponential backoff. Returns (ok, value_or_error_string)."""
    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return True, fn()
        except Exception as exc:  # noqa: BLE001 - anything transient gets retried
            last_err = f"{type(exc).__name__}: {exc}"
            status = getattr(getattr(exc, "response", None), "status_code", None) or \
                     getattr(exc, "status_code", None)
            # 4xx other than 408/429 will not succeed on retry
            if status and 400 <= int(status) < 500 and int(status) not in (408, 429):
                break
            if attempt < MAX_RETRIES:
                wait = BACKOFFS[min(attempt, len(BACKOFFS) - 1)]
                print(f"    retry {attempt + 1}/{MAX_RETRIES} for {label} in {wait}s ({last_err[:120]})")
                time.sleep(wait)
    return False, last_err


def dump_model(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [dump_model(v) for v in value]
    if isinstance(value, dict):
        return {k: dump_model(v) for k, v in value.items()}
    return value


# --------------------------------------------------------------------------------------
# API wrappers
# --------------------------------------------------------------------------------------
def parallel_search(api_key, objective, queries, processor=SEARCH_PROCESSOR,
                    max_results=TOP_N, max_chars=2500):
    payload = {
        "objective": objective,
        "search_queries": queries[:5],
        "processor": processor,
        "max_results": max_results,
        "max_chars_per_result": max_chars,
    }
    r = requests.post(
        SEARCH_URL,
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "parallel-beta": "search-extract-2025-10-10",
        },
        json=payload,
        timeout=90,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("results", data.get("search_results", [])), data


def task_execute(client, probe, output_model, processor):
    res = client.task_run.execute(
        input=probe, output=output_model, processor=processor, timeout=900.0,
    )
    out = res.output
    parsed = getattr(out, "parsed", None)
    return {
        "parsed": dump_model(parsed) if parsed is not None else dump_model(getattr(out, "content", None)),
        "basis": dump_model(getattr(out, "basis", None)) or [],
        "run_id": getattr(res.run, "run_id", None) if getattr(res, "run", None) else None,
    }


def domain_of(url):
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/")[0].lower()


def identity_in_text(text):
    """Return the FIRST identity label whose needle appears (or None)."""
    t = " " + (text or "").lower() + " "
    for label, needles in IDENTITIES.items():
        if any(n in t for n in needles):
            return label
    return None


def identities_in_text(text):
    t = " " + (text or "").lower() + " "
    return [label for label, needles in IDENTITIES.items() if any(n in t for n in needles)]


# --------------------------------------------------------------------------------------
# Smoke test — one Search call + one Task call, raw JSON printed
# --------------------------------------------------------------------------------------
class SmokeEntity(BaseModel):
    canonical_company_name: str = Field(description="Primary company name behind beltfurnaces.com")
    headquarters: str = Field(description="City and country of headquarters.")


def cmd_smoke():
    api_key = get_api_key()
    from parallel import Parallel
    client = Parallel(api_key=api_key)

    print("=== SMOKE 1: Search API (raw JSON) ===")
    _, raw = parallel_search(
        api_key,
        objective="Find manufacturers of continuous conveyor belt furnaces",
        queries=["conveyor belt furnace manufacturer"],
        processor="base", max_results=3, max_chars=600,
    )
    print(json.dumps(raw, indent=2)[:5000])

    print("\n=== SMOKE 2: Task API (raw result) ===")
    res = client.task_run.execute(
        input="beltfurnaces.com", output=SmokeEntity, processor="core", timeout=900.0,
    )
    print(json.dumps(dump_model(res), indent=2, default=str)[:8000])
    print("\nSmoke OK — confirm results[].url/title/excerpts and output.basis shape above.")


# --------------------------------------------------------------------------------------
# Experiment 1 — Discoverability & share of voice (Search API)
# --------------------------------------------------------------------------------------
def run_discoverability(state, api_key):
    all_queries = BUYER_QUERIES + BRANDED_QUERIES
    total = len(all_queries) * RUNS_PER_QUERY
    done = 0
    for q in all_queries:
        for run_i in range(RUNS_PER_QUERY):
            done += 1
            call_id = f"exp1|{q}|run{run_i}"
            if state.has(call_id):
                continue
            print(f"  [{done}/{total}] search: {q!r} run {run_i + 1}")
            ok, val = with_retries(
                lambda: parallel_search(
                    api_key,
                    objective=f"Find manufacturers/suppliers relevant to: {q}",
                    queries=[q],
                ),
                call_id,
            )
            record = {
                "ok": ok, "ts": now_iso(), "api": "search",
                "processor": SEARCH_PROCESSOR, "query": q, "run": run_i,
            }
            if ok:
                results, raw = val
                record["results"] = results
                record["n_results"] = len(results)
            else:
                record["error"] = val
                record["results"] = None
            state.record(call_id, record)


# --------------------------------------------------------------------------------------
# Experiment 2 — Entity resolution (the four-name problem)
# --------------------------------------------------------------------------------------
class EntityResolution(BaseModel):
    canonical_company_name: str = Field(description="Primary company name behind this website.")
    also_known_as: str = Field(description="All alternate/brand names found (XMZ, Torrey Hills, HENGLI, etc.), comma-separated.")
    former_name: str = Field(description="Any former/previous company name, or 'none found'.")
    headquarters: str = Field(description="City and country of headquarters.")
    xmz_same_as_torrey_hills: str = Field(description="Is 'XMZ Technologies' the same company as 'Torrey Hills Technologies'? Yes/No/Unclear + one-line reason.")
    hengli_maker: str = Field(description="Who manufactures HENGLI-brand belt furnaces, and where are they based?")


def run_entity_resolution(state, client):
    total = len(ENTITY_PROBES) * RUNS_PER_QUERY
    done = 0
    for probe in ENTITY_PROBES:
        for run_i in range(RUNS_PER_QUERY):
            done += 1
            call_id = f"exp2|{probe}|run{run_i}"
            if state.has(call_id):
                continue
            print(f"  [{done}/{total}] task/{ENTITY_PROCESSOR}: {probe!r} run {run_i + 1}")
            ok, val = with_retries(
                lambda: task_execute(client, probe, EntityResolution, ENTITY_PROCESSOR),
                call_id,
            )
            record = {
                "ok": ok, "ts": now_iso(), "api": "task",
                "processor": ENTITY_PROCESSOR, "probe": probe, "run": run_i,
            }
            if ok:
                record.update(val)
            else:
                record["error"] = val
                record["parsed"] = None
            state.record(call_id, record)


# --------------------------------------------------------------------------------------
# Experiment 3 — Reputation capture: old name vs new name
# --------------------------------------------------------------------------------------
class CompanyProfile(BaseModel):
    products: str = Field(description="Main product lines / furnace series offered.")
    key_specs: str = Field(description="Hard technical specs found (temperatures, belt widths, zones, throughput).")
    notable_customers: str = Field(description="Named customers/references found. List all.")
    awards_and_certs: str = Field(description="Awards, endorsements, certifications (NASA, ISO, CE, etc.).")
    reputation_summary: str = Field(description="2-3 sentences on how credible/established the company appears.")
    weaknesses_or_gaps: str = Field(description="Anything missing, outdated, inconsistent, or confusing about the online presence.")


def proof_points_in(parsed):
    blob = " ".join(str(v) for v in (parsed or {}).values()).upper()
    return [p for p in PROOF_POINTS if p.upper() in blob]


def run_reputation_profile(state, client):
    total = len(REPUTATION_PROBES) * RUNS_PER_QUERY
    done = 0
    for probe in REPUTATION_PROBES:
        for run_i in range(RUNS_PER_QUERY):
            done += 1
            call_id = f"exp3|{probe}|run{run_i}"
            if state.has(call_id):
                continue
            print(f"  [{done}/{total}] task/{REPUTATION_PROCESSOR}: {probe!r} run {run_i + 1}")
            ok, val = with_retries(
                lambda: task_execute(client, probe, CompanyProfile, REPUTATION_PROCESSOR),
                call_id,
            )
            record = {
                "ok": ok, "ts": now_iso(), "api": "task",
                "processor": REPUTATION_PROCESSOR, "probe": probe, "run": run_i,
            }
            if ok:
                record.update(val)
                record["proof_points_captured"] = proof_points_in(val["parsed"])
            else:
                record["error"] = val
                record["parsed"] = None
            state.record(call_id, record)


def cmd_sweep():
    api_key = get_api_key()
    from parallel import Parallel
    client = Parallel(api_key=api_key)
    state = State()
    state.data["meta"]["config_snapshot"] = config_snapshot()
    print("Experiment 1: discoverability & share of voice ...")
    run_discoverability(state, api_key)
    print("Experiment 2: entity resolution ...")
    run_entity_resolution(state, client)
    print("Experiment 3: reputation capture (old vs new name) ...")
    run_reputation_profile(state, client)
    print_cost_tally(state)
    print(f"\nRaw results -> {RAW_PATH}")


def config_snapshot():
    return {
        "target_domain": TARGET_DOMAIN,
        "buyer_queries": BUYER_QUERIES,
        "branded_queries": BRANDED_QUERIES,
        "entity_probes": ENTITY_PROBES,
        "reputation_probes": REPUTATION_PROBES,
        "runs_per_query": RUNS_PER_QUERY,
        "top_n": TOP_N,
        "processors": {
            "search": SEARCH_PROCESSOR,
            "entity": ENTITY_PROCESSOR,
            "reputation": REPUTATION_PROCESSOR,
        },
        "identities": IDENTITIES,
        "competitor_domains": COMPETITOR_DOMAINS,
        "directory_domains": DIRECTORY_DOMAINS,
        "proof_points": PROOF_POINTS,
    }


def print_cost_tally(state):
    counts = Counter()
    for rec in state.data["calls"].values():
        if rec.get("api") in ("search", "task"):
            counts[(rec.get("api"), rec.get("processor"))] += 1
    print("\n--- Cost tally (ESTIMATED from published rate card, not returned by the API) ---")
    total = 0.0
    for (api, proc), n in sorted(counts.items()):
        rate = PRICE_PER_1K.get((api, proc))
        if rate is None:
            print(f"  {api}/{proc}: {n} calls (no rate on file)")
            continue
        cost = n * rate / 1000.0
        total += cost
        print(f"  {api}/{proc}: {n} calls x ${rate}/1k = ${cost:.2f}")
    print(f"  TOTAL ESTIMATE: ${total:.2f}")
    state.data["meta"]["cost_estimate_usd"] = round(total, 4)
    state.record("_meta_touch", {"ok": True, "ts": now_iso(), "note": "cost tally updated"})


# --------------------------------------------------------------------------------------
# Deterministic site-defect check (no API)
# --------------------------------------------------------------------------------------
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ADDRESS_RE = re.compile(
    r"\d{2,6}\s+[A-Z][A-Za-z .]+(?:Ave|Avenue|St|Street|Rd|Road|Blvd|Boulevard|Dr|Drive|"
    r"Ct|Court|Ln|Lane|Way|Pkwy|Parkway|Suite|Ste)[A-Za-z0-9 .,#-]*", )
MOJIBAKE_RE = re.compile(r"â€™|â€œ|â€\x9d|â€“|â€”|Ã©|Ã¨|Ã¼|Â®|Â©|Ã‚|â€¦|�")
DOUBLED_WORD_RE = re.compile(r"\b(\w+)\s+\1\b", re.IGNORECASE)
KNOWN_MISSPELLINGS = [
    "technolgies", "techologies", "furance", "furnance", "furnce", "manufaturer",
    "manufacterer", "torrey hill ", "temperture", "cermic", "semicondutor",
]


def normalize_phone(p):
    return re.sub(r"\D", "", p)[-10:]


def fetch_page(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (compatible; site-audit)"})
    r.raise_for_status()
    return r.text


def cmd_defects():
    from bs4 import BeautifulSoup

    OUT_DIR.mkdir(exist_ok=True)
    pages = {}
    errors = {}
    print(f"Fetching {SITE_ROOT} ...")
    ok, val = with_retries(lambda: fetch_page(SITE_ROOT), SITE_ROOT)
    if not ok:
        report = {"run_date": now_iso(), "error": f"Could not fetch {SITE_ROOT}: {val}",
                  "pages_checked": [], "defects": None}
        (OUT_DIR / "defects.json").write_text(json.dumps(report, indent=2))
        sys.exit(f"FATAL: cannot fetch {SITE_ROOT}: {val}")
    pages[SITE_ROOT] = val

    # collect same-domain nav links from the homepage
    soup = BeautifulSoup(val, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        u = urljoin(SITE_ROOT, a["href"].split("#")[0])
        p = urlparse(u)
        if p.netloc.replace("www.", "") == TARGET_DOMAIN and u not in links and u != SITE_ROOT:
            if not re.search(r"\.(pdf|jpg|jpeg|png|gif|zip|doc|docx)$", p.path, re.I):
                links.append(u)
    for u in links[:MAX_SITE_PAGES - 1]:
        ok, val = with_retries(lambda u=u: fetch_page(u), u)
        if ok:
            pages[u] = val
        else:
            errors[u] = val
        time.sleep(0.5)

    print(f"Fetched {len(pages)} pages ({len(errors)} failed). Analyzing ...")

    per_page = {}
    phones_by_page, emails_by_page, addrs_by_page = {}, {}, {}
    titles = {}
    for url, html in pages.items():
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
        meta_desc = ""
        md = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        if md:
            meta_desc = (md.get("content") or "").strip()

        phones = sorted({normalize_phone(m) for m in PHONE_RE.findall(text) if len(normalize_phone(m)) == 10})
        emails = sorted({m.lower() for m in EMAIL_RE.findall(text)
                         if not re.search(r"\.(png|jpg|gif|css|js)$", m, re.I)})
        addrs = sorted({re.sub(r"\s+", " ", m).strip() for m in ADDRESS_RE.findall(text)})

        has_ldjson = bool(soup.find("script", attrs={"type": "application/ld+json"}))
        has_microdata = bool(soup.find(attrs={"itemscope": True}))

        head_blob = f"{title} {meta_desc}"
        page_defects = []
        if not title:
            page_defects.append({"type": "missing_title", "detail": "no <title> tag"})
        if not meta_desc:
            page_defects.append({"type": "missing_meta_description", "detail": "no meta description"})
        for pat in KNOWN_MISSPELLINGS:
            if pat in head_blob.lower():
                page_defects.append({"type": "typo_in_title_or_meta", "detail": f"'{pat.strip()}' in: {head_blob[:160]!r}"})
        if MOJIBAKE_RE.search(html):
            sample = MOJIBAKE_RE.search(html).group(0)
            page_defects.append({"type": "encoding_error", "detail": f"mojibake sequence {sample!r} found in page source"})
        dw = DOUBLED_WORD_RE.search(head_blob)
        if dw and dw.group(1).lower() not in {"belt", "the"}:  # allow legit repeats like product names
            page_defects.append({"type": "doubled_word_title_or_meta", "detail": dw.group(0)})
        if not has_ldjson and not has_microdata:
            page_defects.append({"type": "missing_schema_org", "detail": "no JSON-LD or microdata on page"})

        per_page[url] = {
            "title": title, "meta_description": meta_desc,
            "phones": phones, "emails": emails, "addresses": addrs,
            "has_ldjson": has_ldjson, "has_microdata": has_microdata,
            "defects": page_defects,
        }
        phones_by_page[url] = phones
        emails_by_page[url] = emails
        addrs_by_page[url] = addrs
        if title:
            titles.setdefault(title, []).append(url)

    site_defects = []
    all_phones = {p for v in phones_by_page.values() for p in v}
    all_emails = {e for v in emails_by_page.values() for e in v}
    all_addrs = {a for v in addrs_by_page.values() for a in v}
    if len(all_phones) > 1:
        site_defects.append({"type": "nap_phone_inconsistency",
                             "detail": f"{len(all_phones)} distinct phone numbers across pages",
                             "values": sorted(all_phones),
                             "by_page": {u: v for u, v in phones_by_page.items() if v}})
    if len(all_emails) > 1:
        site_defects.append({"type": "nap_email_inconsistency",
                             "detail": f"{len(all_emails)} distinct emails across pages",
                             "values": sorted(all_emails),
                             "by_page": {u: v for u, v in emails_by_page.items() if v}})
    if len(all_addrs) > 1:
        site_defects.append({"type": "nap_address_inconsistency",
                             "detail": f"{len(all_addrs)} distinct street addresses across pages",
                             "values": sorted(all_addrs),
                             "by_page": {u: v for u, v in addrs_by_page.items() if v}})
    dup_titles = {t: us for t, us in titles.items() if len(us) > 1}
    if dup_titles:
        site_defects.append({"type": "duplicate_titles", "detail": f"{len(dup_titles)} titles reused",
                             "values": dup_titles})
    if all(not v["has_ldjson"] and not v["has_microdata"] for v in per_page.values()):
        site_defects.append({"type": "no_schema_org_sitewide",
                             "detail": "no schema.org markup (JSON-LD or microdata) on any checked page"})

    report = {
        "run_date": now_iso(),
        "site": SITE_ROOT,
        "pages_checked": sorted(pages.keys()),
        "pages_failed": errors,
        "site_level_defects": site_defects,
        "per_page": per_page,
    }
    (OUT_DIR / "defects.json").write_text(json.dumps(report, indent=2))
    n_page_defects = sum(len(v["defects"]) for v in per_page.values())
    print(f"defects.json written: {len(site_defects)} site-level, {n_page_defects} page-level defects "
          f"across {len(pages)} pages")


# --------------------------------------------------------------------------------------
# Analysis: metrics, citation breakdown, findings.md, charts
# --------------------------------------------------------------------------------------
def mean_spread(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "spread": None, "n": 0}
    return {
        "mean": round(statistics.mean(vals), 4),
        "spread": round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0,
        "n": len(vals),
    }


def classify_domain(dom):
    dom = dom.lower().removeprefix("www.")
    if any(dom == d or dom.endswith("." + d) for d in OWN_DOMAINS):
        return "own_site"
    if any(dom == d or dom.endswith("." + d) for d in COMPETITOR_DOMAINS):
        return "competitor"
    if any(dom == d or dom.endswith("." + d) for d in DIRECTORY_DOMAINS):
        return "directory"
    return "other"


def result_blob(res):
    excerpts = res.get("excerpts") or []
    if isinstance(excerpts, str):
        excerpts = [excerpts]
    return " ".join([res.get("url") or "", res.get("title") or "", " ".join(excerpts)])


def analyze_exp1(state):
    """Per-query appearance rates (per identity + overall), ranks, share of voice."""
    per_query = {}
    for q in BUYER_QUERIES + BRANDED_QUERIES:
        runs = [v for k, v in sorted(state.calls(f"exp1|{q}|").items())]
        run_stats = []
        for rec in runs:
            if not rec.get("ok") or rec.get("results") is None:
                run_stats.append(None)
                continue
            results = rec["results"][:TOP_N]
            found_rank, ident_hits = None, Counter()
            target_hits, competitor_hits = 0, 0
            for i, res in enumerate(results, start=1):
                dom = domain_of(res.get("url", ""))
                blob = result_blob(res)
                cls = classify_domain(dom)
                if cls == "own_site":
                    target_hits += 1
                elif cls == "competitor":
                    competitor_hits += 1
                idents = identities_in_text(blob)
                for lb in idents:
                    ident_hits[lb] += 1
                if (cls == "own_site" or idents) and found_rank is None:
                    found_rank = i
            run_stats.append({
                "found": found_rank is not None, "rank": found_rank,
                "identity_hits": dict(ident_hits),
                "target_results": target_hits, "competitor_results": competitor_hits,
            })
        ok_runs = [r for r in run_stats if r is not None]
        appearance = mean_spread([1.0 if r["found"] else 0.0 for r in ok_runs])
        ranks = [r["rank"] for r in ok_runs if r["rank"]]
        ident_totals = Counter()
        for r in ok_runs:
            ident_totals.update(r["identity_hits"])
        per_query[q] = {
            "kind": "branded" if q in BRANDED_QUERIES else "buyer",
            "runs_attempted": len(runs), "runs_ok": len(ok_runs),
            "appearance_rate": appearance,
            "mean_rank": round(statistics.mean(ranks), 2) if ranks else None,
            "identity_hits_total": dict(ident_totals),
            "target_results_per_run": mean_spread([r["target_results"] for r in ok_runs]),
            "competitor_results_per_run": mean_spread([r["competitor_results"] for r in ok_runs]),
            "per_run": run_stats,
        }
    return per_query


def analyze_share_of_voice(state):
    """Result-level share of voice per run across buyer queries only."""
    per_run_sov = []
    domain_counts = Counter()
    for q in BUYER_QUERIES:
        for k, rec in sorted(state.calls(f"exp1|{q}|").items()):
            if not rec.get("ok") or rec.get("results") is None:
                per_run_sov.append(None)
                continue
            tgt = comp = 0
            for res in rec["results"][:TOP_N]:
                dom = domain_of(res.get("url", ""))
                cls = classify_domain(dom)
                domain_counts[dom] += 1
                if cls == "own_site":
                    tgt += 1
                elif cls == "competitor":
                    comp += 1
            per_run_sov.append(tgt / (tgt + comp) if (tgt + comp) else None)
    return mean_spread([v for v in per_run_sov if v is not None]), domain_counts


def analyze_exp2(state):
    probes = {}
    yes_flags = []
    for probe in ENTITY_PROBES:
        runs = [v for k, v in sorted(state.calls(f"exp2|{probe}|").items())]
        parsed_runs = [r.get("parsed") for r in runs if r.get("ok") and r.get("parsed")]
        answers = []
        for p in parsed_runs:
            ans = str(p.get("xmz_same_as_torrey_hills", "")).strip()
            answers.append(ans)
            yes_flags.append(1.0 if ans.lower().startswith("yes") else 0.0)
        probes[probe] = {
            "runs_attempted": len(runs), "runs_ok": len(parsed_runs),
            "canonical_names": [p.get("canonical_company_name") for p in parsed_runs],
            "former_names": [p.get("former_name") for p in parsed_runs],
            "xmz_same_as_torrey_hills": answers,
            "hengli_maker": [p.get("hengli_maker") for p in parsed_runs],
            "parsed_runs": parsed_runs,
        }
    linkage = mean_spread(yes_flags)
    if linkage["n"] == 0:
        verdict = "no_data"
    elif linkage["mean"] >= 0.8:
        verdict = "resolved"
    elif linkage["mean"] >= 0.4:
        verdict = "partially_resolved"
    else:
        verdict = "fragmented"
    return probes, {"verdict": verdict, "xmz_torrey_linkage_rate": linkage}


def analyze_exp3(state):
    out = {}
    for probe in REPUTATION_PROBES:
        runs = [v for k, v in sorted(state.calls(f"exp3|{probe}|").items())]
        ok_runs = [r for r in runs if r.get("ok") and r.get("parsed")]
        rates = [len(r.get("proof_points_captured", [])) / len(PROOF_POINTS) for r in ok_runs]
        captured_union = sorted({p for r in ok_runs for p in r.get("proof_points_captured", [])})
        out[probe] = {
            "runs_attempted": len(runs), "runs_ok": len(ok_runs),
            "proof_capture_rate": mean_spread(rates),
            "proof_points_union": captured_union,
            "per_run_captured": [r.get("proof_points_captured") for r in ok_runs],
        }
    return out


def analyze_citations(state):
    """Classify every Task-basis citation domain."""
    breakdown = Counter()
    domains = Counter()
    total = 0
    for rec in state.data["calls"].values():
        if rec.get("api") != "task" or not rec.get("ok"):
            continue
        for fb in rec.get("basis") or []:
            for cit in (fb.get("citations") or []) if isinstance(fb, dict) else []:
                url = cit.get("url") if isinstance(cit, dict) else None
                if not url:
                    continue
                dom = domain_of(url)
                breakdown[classify_domain(dom)] += 1
                domains[dom] += 1
                total += 1
    pct = {k: round(v / total, 4) for k, v in breakdown.items()} if total else {}
    return {
        "total_citations": total,
        "counts": dict(breakdown),
        "shares": pct,
        "top_domains": domains.most_common(25),
    }


def identity_appearance_rates(per_query):
    """Per-run appearance rate of each identity across ALL queries -> mean ± spread."""
    rates = {label: [] for label in IDENTITIES}
    for q, m in per_query.items():
        for run in m["per_run"]:
            if run is None:
                continue
            for label in IDENTITIES:
                rates[label].append(1.0 if run["identity_hits"].get(label) else 0.0)
    return {label: mean_spread(vals) for label, vals in rates.items()}


def name_fragmentation(per_query):
    """1 - (share of the most-used identity among all identity mentions). 0 = one
    consistent name; ->1 = mentions split across identities."""
    totals = Counter()
    for m in per_query.values():
        totals.update(m["identity_hits_total"])
    total = sum(totals.values())
    if not total:
        return {"score": None, "distribution": {}, "note": "no identity mentions observed"}
    dist = {k: round(v / total, 4) for k, v in totals.items()}
    return {"score": round(1 - max(totals.values()) / total, 4),
            "distribution": dist, "total_mentions": total}


def cmd_analyze():
    state = State()
    if not state.data["calls"]:
        sys.exit("FATAL: out/raw_results.json has no calls. Run `sweep` first.")

    per_query = analyze_exp1(state)
    sov, domain_counts = analyze_share_of_voice(state)
    exp2, entity_verdict = analyze_exp2(state)
    exp3 = analyze_exp3(state)
    citations = analyze_citations(state)
    ident_rates = identity_appearance_rates(per_query)
    frag = name_fragmentation(per_query)

    th = exp3.get(REPUTATION_PROBES[0], {}).get("proof_capture_rate", {})
    xmz = exp3.get(REPUTATION_PROBES[1], {}).get("proof_capture_rate", {})
    delta = (round(xmz["mean"] - th["mean"], 4)
             if th.get("mean") is not None and xmz.get("mean") is not None else None)

    summary = {
        "run_date": now_iso(),
        "new_name_appearance_rate": ident_rates.get("XMZ (new brand)"),
        "old_name_appearance_rate": ident_rates.get("Torrey Hills (old)"),
        "hengli_appearance_rate": ident_rates.get("HENGLI (product brand)"),
        "name_fragmentation": frag,
        "share_of_voice": sov,
        "proof_capture": {"torrey_hills": th, "xmz": xmz, "delta": delta},
        "entity_resolution_verdict": entity_verdict,
        "citation_source_breakdown": citations,
        "cost_estimate_usd": state.data["meta"].get("cost_estimate_usd"),
        "config_snapshot": state.data["meta"].get("config_snapshot") or config_snapshot(),
    }
    (OUT_DIR / "metrics_summary.json").write_text(json.dumps(summary, indent=2))

    detail = {
        "run_date": now_iso(),
        "per_query": per_query, "share_of_voice_domains": domain_counts.most_common(40),
        "entity_resolution": exp2, "reputation": exp3,
    }
    (OUT_DIR / "derived_detail.json").write_text(json.dumps(detail, indent=2))

    write_findings(summary, per_query, exp2, exp3, citations)
    make_charts(per_query, frag, domain_counts, exp3)
    print("Wrote out/metrics_summary.json, out/derived_detail.json, out/findings.md, out/charts/*.png")

    print("\n===== HEADLINES =====")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("config_snapshot", "citation_source_breakdown")}, indent=2)[:2500])


def fmt_ms(ms):
    if not ms or ms.get("mean") is None:
        return "no data"
    return f"{ms['mean']:.0%} ± {ms['spread']:.0%} (n={ms['n']})"


def sev(rank):
    return {1: "HIGH", 2: "MEDIUM", 3: "LOW"}[rank]


def write_findings(summary, per_query, exp2, exp3, citations):
    L = []
    L.append(f"# AI Visibility Findings — beltfurnaces.com (XMZ / Torrey Hills / HENGLI)")
    L.append(f"\nGenerated {summary['run_date']} from `out/raw_results.json`. Factual observations only;"
             f" no recommendations. Metrics are rate ± sample stdev across repeated runs"
             f" (RUNS_PER_QUERY={RUNS_PER_QUERY}).\n")

    n = 0

    def finding(title, severity, body):
        nonlocal n
        n += 1
        L.append(f"## Finding {n} — {title}  `[{severity}]`\n")
        L.append(body + "\n")

    new_r, old_r = summary["new_name_appearance_rate"], summary["old_name_appearance_rate"]
    hen_r = summary["hengli_appearance_rate"]
    finding(
        "Name appearance rates across all search runs",
        "HIGH",
        f"Across every search run (buyer + branded queries), the rate at which each identity"
        f" appeared anywhere in retrieved results:\n\n"
        f"- **XMZ (new brand):** {fmt_ms(new_r)}\n"
        f"- **Torrey Hills (old name):** {fmt_ms(old_r)}\n"
        f"- **HENGLI (product brand):** {fmt_ms(hen_r)}\n\n"
        f"Evidence: per-run identity hits in `out/raw_results.json` (exp1 records) and"
        f" `out/derived_detail.json` per_query.per_run.",
    )

    frag = summary["name_fragmentation"]
    finding(
        "Name fragmentation",
        "HIGH" if (frag.get("score") or 0) > 0.3 else "MEDIUM",
        f"Identity-mention distribution across all retrieved results: "
        f"`{json.dumps(frag.get('distribution', {}))}` (total mentions:"
        f" {frag.get('total_mentions')}). Fragmentation score (1 − top-name share):"
        f" **{frag.get('score')}**. 0 means one consistent name; values toward 1 mean the"
        f" company's mentions are split across identities.",
    )

    sov = summary["share_of_voice"]
    finding(
        "Share of voice vs named competitors (buyer queries)",
        "HIGH" if (sov.get("mean") is not None and sov["mean"] < 0.25) else "MEDIUM",
        f"Own-site results ÷ (own-site + competitor results) per run, buyer-intent queries only:"
        f" **{fmt_ms(sov)}**. Domain-level counts are in `out/derived_detail.json`"
        f" share_of_voice_domains.",
    )

    ev = summary["entity_resolution_verdict"]
    ans_lines = []
    for probe, m in exp2.items():
        for a in m["xmz_same_as_torrey_hills"]:
            ans_lines.append(f"  - probe `{probe}`: \"{a}\"")
    finding(
        f"Entity resolution verdict: {ev['verdict']}",
        "HIGH" if ev["verdict"] != "resolved" else "LOW",
        f"Rate at which the Task API affirmed 'XMZ Technologies' = 'Torrey Hills Technologies':"
        f" **{fmt_ms(ev['xmz_torrey_linkage_rate'])}**. The model's own answers:\n\n"
        + "\n".join(ans_lines) +
        f"\n\nCanonical names returned per probe (from parsed output):\n\n"
        + "\n".join(f"  - `{p}` → {sorted(set(str(x) for x in m['canonical_names']))}" for p, m in exp2.items()),
    )

    pc = summary["proof_capture"]
    th_union = exp3.get(REPUTATION_PROBES[0], {}).get("proof_points_union", [])
    xz_union = exp3.get(REPUTATION_PROBES[1], {}).get("proof_points_union", [])
    finding(
        "Proof-point capture: old name vs new name",
        "HIGH" if (pc["delta"] is not None and pc["delta"] < 0) else "MEDIUM",
        f"Share of {len(PROOF_POINTS)} tracked proof points ({', '.join(PROOF_POINTS)}) surfaced"
        f" by deep research runs:\n\n"
        f"- **'{REPUTATION_PROBES[0]}':** {fmt_ms(pc['torrey_hills'])} — union captured: {th_union}\n"
        f"- **'{REPUTATION_PROBES[1]}':** {fmt_ms(pc['xmz'])} — union captured: {xz_union}\n"
        f"- **Delta (new − old):** {pc['delta']}\n\n"
        f"Full parsed outputs incl. the model's reputation_summary and weaknesses_or_gaps"
        f" fields are in `out/raw_results.json` (exp3 records).",
    )

    cit = citations
    finding(
        "Citation source mix in Task-API evidence",
        "MEDIUM",
        f"Across **{cit['total_citations']}** basis citations from all Task runs:"
        f" `{json.dumps(cit['shares'])}` (counts: `{json.dumps(cit['counts'])}`)."
        f" Top cited domains:\n\n"
        + "\n".join(f"  - {d} × {c}" for d, c in cit["top_domains"][:12]),
    )

    # per-query appearance table
    L.append("## Appendix — appearance rate by query\n")
    L.append("| Query | Kind | Appearance | Mean rank | Identity hits |")
    L.append("|---|---|---|---|---|")
    for q, m in per_query.items():
        L.append(f"| {q} | {m['kind']} | {fmt_ms(m['appearance_rate'])} | {m['mean_rank']} |"
                 f" {json.dumps(m['identity_hits_total'])} |")
    L.append("")

    (OUT_DIR / "findings.md").write_text("\n".join(L))


# --------------------------------------------------------------------------------------
# Charts (matplotlib, one PNG each)
# --------------------------------------------------------------------------------------
PALETTE = {
    "torrey": "#2a78d6",   # blue  — old name
    "xmz": "#1baf7a",      # aqua  — new brand
    "hengli": "#eda100",   # yellow— product brand
    "target": "#2a78d6",
    "muted": "#c3c2b7",
    "surface": "#fcfcfb",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
}
IDENT_COLOR = {
    "Torrey Hills (old)": PALETTE["torrey"],
    "XMZ (new brand)": PALETTE["xmz"],
    "HENGLI (product brand)": PALETTE["hengli"],
}


def _style_ax(ax):
    ax.set_facecolor(PALETTE["surface"])
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(PALETTE["axis"])
    ax.tick_params(colors=PALETTE["ink2"], labelsize=9)
    ax.xaxis.label.set_color(PALETTE["ink2"])
    ax.yaxis.label.set_color(PALETTE["ink2"])
    ax.title.set_color(PALETTE["ink"])


def make_charts(per_query, frag, domain_counts, exp3):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.family"] = "sans-serif"

    # 1 — appearance rate by query (horizontal bars + spread whiskers)
    qs = list(per_query.keys())
    means = [per_query[q]["appearance_rate"]["mean"] or 0 for q in qs]
    spreads = [per_query[q]["appearance_rate"]["spread"] or 0 for q in qs]
    colors = [PALETTE["xmz"] if per_query[q]["kind"] == "branded" else PALETTE["torrey"] for q in qs]
    fig, ax = plt.subplots(figsize=(9, 0.42 * len(qs) + 1.6), facecolor=PALETTE["surface"])
    y = range(len(qs))
    ax.barh(y, means, xerr=spreads, color=colors, height=0.62,
            error_kw={"ecolor": PALETTE["ink2"], "capsize": 3, "lw": 1})
    ax.set_yticks(list(y), [q if len(q) <= 48 else q[:46] + "…" for q in qs])
    ax.invert_yaxis()
    ax.set_xlim(0, 1.05)
    ax.set_xlabel("appearance rate (share of runs where company surfaced)")
    ax.set_title(f"Company appearance rate by query (n={RUNS_PER_QUERY} runs/query)", loc="left", fontsize=11)
    ax.xaxis.grid(True, color=PALETTE["grid"], lw=0.7)
    ax.set_axisbelow(True)
    _style_ax(ax)
    import matplotlib.patches as mpatches
    ax.legend(handles=[mpatches.Patch(color=PALETTE["torrey"], label="buyer-intent query"),
                       mpatches.Patch(color=PALETTE["xmz"], label="branded query")],
              frameon=False, fontsize=9, loc="lower right", labelcolor=PALETTE["ink2"])
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "appearance_rate_by_query.png", dpi=160)
    plt.close(fig)

    # 2 — name-used distribution
    dist = frag.get("distribution", {})
    labels = list(IDENTITIES.keys())
    vals = [dist.get(lb, 0) for lb in labels]
    fig, ax = plt.subplots(figsize=(7, 4.2), facecolor=PALETTE["surface"])
    bars = ax.bar(labels, vals, color=[IDENT_COLOR[lb] for lb in labels], width=0.55)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.015, f"{v:.0%}",
                ha="center", fontsize=10, color=PALETTE["ink"])
    ax.set_ylim(0, max(vals + [0.1]) * 1.25)
    ax.set_ylabel("share of all identity mentions in retrieved results")
    score = frag.get("score")
    ax.set_title(f"Which name does AI retrieval use? (fragmentation score: {score})",
                 loc="left", fontsize=11)
    ax.yaxis.grid(True, color=PALETTE["grid"], lw=0.7)
    ax.set_axisbelow(True)
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "name_used_distribution.png", dpi=160)
    plt.close(fig)

    # 3 — share of voice vs competitors (top domains by result count, target highlighted)
    top = [(d, c) for d, c in domain_counts.most_common(60)
           if classify_domain(d) in ("own_site", "competitor")][:12]
    if top:
        doms = [d for d, _ in top]
        cnts = [c for _, c in top]
        cols = [PALETTE["xmz"] if classify_domain(d) == "own_site" else PALETTE["muted"] for d in doms]
        fig, ax = plt.subplots(figsize=(8.5, 0.42 * len(doms) + 1.6), facecolor=PALETTE["surface"])
        y = range(len(doms))
        ax.barh(y, cnts, color=cols, height=0.62)
        ax.set_yticks(list(y), doms)
        ax.invert_yaxis()
        ax.set_xlabel("results retrieved across all buyer-query runs")
        ax.set_title("Share of voice: beltfurnaces.com (green) vs competitor domains",
                     loc="left", fontsize=11)
        for yi, c in zip(y, cnts):
            ax.text(c + max(cnts) * 0.01, yi, str(c), va="center", fontsize=9, color=PALETTE["ink2"])
        ax.xaxis.grid(True, color=PALETTE["grid"], lw=0.7)
        ax.set_axisbelow(True)
        _style_ax(ax)
        fig.tight_layout()
        fig.savefig(CHARTS_DIR / "share_of_voice.png", dpi=160)
        plt.close(fig)

    # 4 — proof-point capture, old vs new name
    names = ["Torrey Hills\n(old name)", "XMZ\n(new name)"]
    caps = [exp3.get(p, {}).get("proof_capture_rate", {}) for p in REPUTATION_PROBES]
    means = [c.get("mean") or 0 for c in caps]
    spreads = [c.get("spread") or 0 for c in caps]
    fig, ax = plt.subplots(figsize=(6, 4.2), facecolor=PALETTE["surface"])
    bars = ax.bar(names, means, yerr=spreads, color=[PALETTE["torrey"], PALETTE["xmz"]],
                  width=0.5, error_kw={"ecolor": PALETTE["ink2"], "capsize": 4, "lw": 1})
    for b, v in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.0%}",
                ha="center", fontsize=11, color=PALETTE["ink"])
    ax.set_ylim(0, max(means + [0.2]) * 1.35)
    ax.set_ylabel(f"share of {len(PROOF_POINTS)} proof points surfaced")
    ax.set_title("Reputation capture: research under old vs new name", loc="left", fontsize=11)
    ax.yaxis.grid(True, color=PALETTE["grid"], lw=0.7)
    ax.set_axisbelow(True)
    _style_ax(ax)
    fig.tight_layout()
    fig.savefig(CHARTS_DIR / "proof_point_capture.png", dpi=160)
    plt.close(fig)


# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("command", choices=["smoke", "sweep", "defects", "analyze", "all"])
    args = ap.parse_args()
    if args.command == "smoke":
        cmd_smoke()
    elif args.command == "sweep":
        cmd_sweep()
    elif args.command == "defects":
        cmd_defects()
    elif args.command == "analyze":
        cmd_analyze()
    elif args.command == "all":
        cmd_sweep()
        cmd_defects()
        cmd_analyze()


if __name__ == "__main__":
    main()
