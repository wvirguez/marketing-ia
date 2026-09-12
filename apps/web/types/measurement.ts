// Mirrors apps/api/app/measurement/schemas.py — only the contracts MVP-09B
// actually uses (GET/POST /metrics). No field here represents a provider,
// currency, unit, percentage, actor, notes, metadata, status, confidence,
// or verification concept — none of those exist on the backend either.
//
// `values` uses `string | number` because the backend's Decimal fields may
// serialize as either depending on the exact value — this type never
// assumes one shape over the other.

export type MetricSource = "MANUAL" | "IMPORTED" | "PLATFORM";

export interface MetricEntryPublic {
  id: string;
  period_start: string;
  period_end: string;
  channel: string;
  source: MetricSource;
  values: Record<string, string | number>;
  is_current: boolean;
  created_at: string;
}

export interface MetricEntryListResponse {
  items: MetricEntryPublic[];
}

export interface MetricEntryWriteRequest {
  period_start: string;
  period_end: string;
  channel: string;
  source: MetricSource;
  client_request_id: string;
  values: Record<string, string | number>;
}
