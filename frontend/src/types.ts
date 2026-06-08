// Mirrors the FastAPI response models.

export interface CompanyProfile {
  one_liner: string | null;
  product_lines: string[];
  buyer_queries: string[];
  competitors: string[];
}

export interface ProfileResponse {
  domain: string;
  profile: CompanyProfile;
  basis: Array<Record<string, unknown>> | null;
}

export interface CompetitorHit {
  name: string;
  rank: number;
}

export interface QueryResult {
  query: string;
  own_domain_present: boolean;
  name_mentioned: boolean;
  appeared: boolean;
  rank: number | null;
  competitors_appeared: CompetitorHit[];
  result_count: number;
}

export interface ScoreBreakdown {
  presence_rate: number;
  share_of_voice: number;
  rank_quality: number;
  avg_rank: number | null;
  index: number;
}

export interface ScanResponse {
  company: string;
  domain: string;
  per_query: QueryResult[];
  score: ScoreBreakdown;
  blind_spots: QueryResult[];
}
