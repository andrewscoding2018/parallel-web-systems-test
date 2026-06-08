import type { QueryResult } from "../types";

function Signal({ ok, label, title }: { ok: boolean; label: string; title: string }) {
  return (
    <span className={`signal ${ok ? "yes" : "no"}`} title={title}>
      {ok ? "✓" : "—"} {label}
    </span>
  );
}

export function QueryTable({ rows, company }: { rows: QueryResult[]; company: string }) {
  return (
    <section className="card">
      <h2>Per-query breakdown</h2>
      <p className="muted">
        For each query a buyer (or their AI agent) would ask, did <strong>{company}</strong> show
        up — on its own site, or mentioned on a third-party page — and who showed up instead?
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Query</th>
              <th>Appeared</th>
              <th>Rank</th>
              <th>Competitors that appeared</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.query} className={r.appeared ? "" : "row-absent"}>
                <td className="q-cell">{r.query}</td>
                <td>
                  <div className="signals">
                    <Signal
                      ok={r.own_domain_present}
                      label="own site"
                      title="Company domain present in a result URL"
                    />
                    <Signal
                      ok={r.name_mentioned}
                      label="mention"
                      title="Company name mentioned in a title or excerpt (third-party pages count)"
                    />
                  </div>
                </td>
                <td>{r.rank === null ? "—" : `#${r.rank + 1}`}</td>
                <td>
                  {r.competitors_appeared.length === 0 ? (
                    <span className="muted">none detected</span>
                  ) : (
                    <div className="chips">
                      {r.competitors_appeared.map((c) => (
                        <span key={c.name} className="chip" title={`First seen at rank #${c.rank + 1}`}>
                          {c.name} <span className="chip-rank">#{c.rank + 1}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
