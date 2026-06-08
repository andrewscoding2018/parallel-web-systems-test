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
      <header className="topbar">
        <div className="brand">
          <h1>AI Visibility Index</h1>
          <p>Search a company domain to measure AI search visibility.</p>
        </div>
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
              className="ghost-btn"
              onClick={() => setDomain(EXAMPLE)}
              disabled={busy}
            >
              try {EXAMPLE}
            </button>
          )}
        </form>
      </header>

      <main className="results">
        <Progress stage={stage} />

        {error && (
          <div className="card error-card">
            <strong>Something went wrong.</strong>
            <p>{error}</p>
          </div>
        )}

        {!profile && !scan && !error && stage === "idle" && (
          <section className="empty-state">
            <p>Enter a domain to generate buyer queries, competitors, and a visibility score.</p>
          </section>
        )}

        {scan && (
          <>
            <ScoreCard score={scan.score} />
            <BlindSpots rows={scan.blind_spots} company={scan.company} />
            <QueryTable rows={scan.per_query} company={scan.company} />
          </>
        )}

        {profile && <ProfilePanel data={profile} />}
      </main>
    </div>
  );
}

function Progress({ stage }: { stage: Stage }) {
  if (stage === "idle" || stage === "error" || stage === "done") return null;
  const steps = [
    { key: "profiling", label: "Generating buyer queries and competitors" },
    { key: "scanning", label: "Searching and scoring visibility" },
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
