import type { ProfileResponse, ScanResponse } from "./types";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) detail = data.detail;
    } catch {
      /* ignore parse errors, keep status message */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

/** Stage 1 — deep dive. */
export function fetchProfile(domain: string): Promise<ProfileResponse> {
  return post<ProfileResponse>("/api/profile", { domain });
}

/** Stage 2 — topical comparison + scoring. */
export function runScan(params: {
  domain: string;
  company_name?: string;
  buyer_queries: string[];
  competitors: string[];
}): Promise<ScanResponse> {
  return post<ScanResponse>("/api/scan", params);
}
