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

// MVP-11D-B: mirrors apps/api/app/measurement/schemas.py's Measurement
// Analysis contracts (GET /analysis, POST /analysis/run). `value` uses
// `string | number` for the same reason as MetricEntryPublic.values above
// — it is the same backend Decimal family. No field here represents a
// grade, percentage, recommendation, or causal claim — the backend
// returns none of those either.

export type MeasurementAnalysisRunStatus = "RUNNING" | "COMPLETED" | "FAILED";

export interface MeasurementAnalysisRunPublic {
  id: string;
  campaign_id: string;
  client_request_id: string;
  status: MeasurementAnalysisRunStatus;
  failure_reason: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface MeasurementAnalysisRunTriggerRequest {
  client_request_id: string;
}

export interface PerformanceObservationPublic {
  id: string;
  metric_name: string;
  value: string | number;
  source_metric_entry_ids: string[];
  created_at: string;
}

export interface PerformanceSignalPublic {
  id: string;
  summary: string;
  source_observation_ids: string[];
  created_at: string;
}

export interface AnalysisResultPublic {
  id: string;
  summary: string;
  source_signal_ids: string[];
  created_at: string;
}

export interface AnalysisResponse {
  observations: PerformanceObservationPublic[];
  signals: PerformanceSignalPublic[];
  analysis_results: AnalysisResultPublic[];
}
