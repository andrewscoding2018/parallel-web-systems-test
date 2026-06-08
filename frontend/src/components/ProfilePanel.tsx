import type { ProfileResponse } from "../types";

export function ProfilePanel({ data }: { data: ProfileResponse }) {
  const { profile, basis } = data;
  return (
    <section className="card profile-panel">
      <h2>Deep dive — {data.domain}</h2>
      {profile.one_liner && <p className="one-liner">{profile.one_liner}</p>}

      <div className="profile-grid">
        <div>
          <h3>Product lines</h3>
          <ul className="tag-list">
            {profile.product_lines.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Competitors</h3>
          <ul className="tag-list">
            {profile.competitors.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      </div>

      <h3>Generated buyer queries ({profile.buyer_queries.length})</h3>
      <ul className="query-list">
        {profile.buyer_queries.map((q) => (
          <li key={q}>{q}</li>
        ))}
      </ul>

      {basis && basis.length > 0 && (
        <details className="basis">
          <summary>Sources &amp; reasoning (Parallel Basis) — {basis.length} field(s)</summary>
          <pre>{JSON.stringify(basis, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}
