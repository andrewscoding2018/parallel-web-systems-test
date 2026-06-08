import { useState } from "react";
import { fetchProfile, runScan } from "./api";
import type { ProfileResponse, ScanResponse } from "./types";
import { ProfilePanel } from "./components/ProfilePanel";
import { ScoreCard } from "./components/ScoreCard";
import { QueryTable } from "./components/QueryTable";
import { BlindSpots } from "./components/BlindSpots";

type Stage = "idle" | "profiling" | "scanning" | "done" | "error";

const EXAMPLE = "torreyhillstech.com";

export default function App() {
  const [domain, setDomain] = useState("");
  const [stage, setStage] = useState<Stage>("idle");
  const [error, setError] = useState<string | null>(null);
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [scan, setScan] = useState<ScanResponse | null>(null);

  const busy = stage === "profiling" || stage === "scanning";

  async function handleRun(e: React.FormEvent) {
    e.preventDefault();
    const d = domain.trim();
    if (!d) return;

    setError(null);
    setProfile(null);
    setScan(null);

    try {
      // Stage 1 — deep dive.
      setStage("profiling");
      const prof = await fetchProfile(d);
      setProfile(prof);

      // Stage 2 — topical comparison.
      setStage("scanning");
      const result = await runScan({
        domain: d,
        // Use the brand name the deep dive discovered (e.g. "Torrey Hills
        // Technologies") so third-party mentions match. Empty -> backend derives.
        company_name: prof.profile.company_name ?? undefined,
        buyer_queries: prof.profile.buyer_queries,
        competitors: prof.profile.competitors,
      });
      setScan(result);
      setStage("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStage("error");
    }
  }

  return (
    <div className="app">
      <header className="hero">
        <h1>AI Visibility Index</h1>
        <p className="tagline">
          How visible is a company to AI assistants — and where is it invisible? Buyers increasingly
          ask an AI instead of Googling. This runs a company's category queries through the{" "}
          <a href="https://parallel.ai" target="_blank" rel="noreferrer">
            Parallel
          </a>{" "}
          Search API — the same kind of web context assistants consume — and measures where it
          surfaces versus its competitors.
        </p>

        <form className="run-form" onSubmit={handleRun}>
          <input
            type="text"
            placeholder="company domain, e.g. torreyhillstech.com"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            disabled={busy}
            aria-label="Company domain"
          />
          <button type="submit" disabled={busy || !domain.trim()}>
            {busy ? "Analyzing…" : "Measure visibility"}
          </button>
          {!domain && (
            <button
              type="button"
              className="link-btn"
              onClick={() => setDomain(EXAMPLE)}
              disabled={busy}
            >
              try {EXAMPLE}
            </button>
          )}
        </form>

        <Progress stage={stage} />
      </header>

      {error && (
        <div className="card error-card">
          <strong>Something went wrong.</strong>
          <p>{error}</p>
        </div>
      )}

      {scan && (
        <>
          <ScoreCard score={scan.score} />
          <BlindSpots rows={scan.blind_spots} company={scan.company} />
          <QueryTable rows={scan.per_query} company={scan.company} />
        </>
      )}

      {profile && <ProfilePanel data={profile} />}

      <footer className="footer">
        Built on the Parallel Web Systems API. Stage 1 (deep dive) uses the Task API; Stage 2
        (comparison) uses the Search API. Evidence-first: sources and the scoring formula are shown.
      </footer>
    </div>
  );
}

function Progress({ stage }: { stage: Stage }) {
  if (stage === "idle" || stage === "error" || stage === "done") return null;
  const steps = [
    { key: "profiling", label: "Deep dive — generating buyer queries & competitors (Task API)" },
    { key: "scanning", label: "Topical comparison — running searches & scoring (Search API)" },
  ];
  return (
    <div className="progress">
      {steps.map((s) => {
        const active = s.key === stage;
        const done =
          (s.key === "profiling" && stage === "scanning");
        return (
          <div key={s.key} className={`progress-step ${active ? "active" : ""} ${done ? "done" : ""}`}>
            <span className="spinner-dot" />
            {s.label}
          </div>
        );
      })}
    </div>
  );
}
