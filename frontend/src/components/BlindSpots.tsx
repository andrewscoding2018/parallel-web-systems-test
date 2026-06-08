import type { QueryResult } from "../types";

export function BlindSpots({ rows, company }: { rows: QueryResult[]; company: string }) {
  if (rows.length === 0) {
    return (
      <section className="card">
        <h2>Blind spots</h2>
        <p className="muted">
          None. There were no queries where a competitor appeared but {company} was absent.
        </p>
      </section>
    );
  }

  return (
    <section className="card blind-spots">
      <h2>Blind spots — highest-value fixes</h2>
      <p className="muted">
        Queries where <strong>{company}</strong> is invisible but a competitor surfaces. An AI
        assistant asked these questions literally cannot recommend {company}.
      </p>
      <ul>
        {rows.map((r) => (
          <li key={r.query}>
            <div className="blind-q">{r.query}</div>
            <div className="blind-competitors">
              Winning instead:{" "}
              {r.competitors_appeared.map((c) => c.name).join(", ") || "(unnamed competitors)"}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
