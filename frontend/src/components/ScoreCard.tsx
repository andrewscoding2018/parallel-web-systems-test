import type { ScoreBreakdown } from "../types";

const pct = (v: number) => `${Math.round(v * 100)}%`;

function scoreColor(index: number): string {
  if (index >= 67) return "var(--good)";
  if (index >= 34) return "var(--mid)";
  return "var(--bad)";
}

export function ScoreCard({ score }: { score: ScoreBreakdown }) {
  return (
    <section className="card score-card">
      <div className="score-dial" style={{ color: scoreColor(score.index) }}>
        <div className="score-number">{score.index}</div>
        <div className="score-label">AI Visibility Index</div>
        <div className="score-scale">out of 100</div>
      </div>

      <div className="score-components">
        <Component
          label="Presence rate"
          value={pct(score.presence_rate)}
          help="Share of queries where the company appeared at all"
        />
        <Component
          label="Share of voice"
          value={pct(score.share_of_voice)}
          help="Company appearances ÷ (company + competitor) appearances"
        />
        <Component
          label="Rank quality"
          value={pct(score.rank_quality)}
          help={
            score.avg_rank === null
              ? "No appearances to rank"
              : `Avg rank ${score.avg_rank.toFixed(1)} when present (lower is better)`
          }
        />
      </div>

      <p className="formula">
        <code>
          index = 0.5·presence + 0.3·share_of_voice + 0.2·rank_quality
        </code>
        <span className="formula-note">
          The formula is shown on purpose — no black box.
        </span>
      </p>
    </section>
  );
}

function Component({ label, value, help }: { label: string; value: string; help: string }) {
  return (
    <div className="component">
      <div className="component-value">{value}</div>
      <div className="component-label">{label}</div>
      <div className="component-help">{help}</div>
    </div>
  );
}
