// Mirrors apps/api/app/research/schemas.py exactly. Research and Audience
// share one backend bounded context/router (app/research/*), so their
// public types are kept together here too, the same way types/campaign.ts
// bundles every entity from campaigns/schemas.py in one file. No internal
// UUID, no workspace_id — only public_id-derived fields.

export type SourceType = "ARTICLE" | "REPORT" | "FORUM" | "SOCIAL" | "SURVEY" | "OTHER";

export interface ResearchSourcePublic {
  id: string;
  source_type: SourceType;
  title: string;
  locator: string | null;
  publisher: string | null;
  excerpt: string | null;
  retrieved_at: string | null;
  created_at: string;
}

export interface ResearchReportPublic {
  id: string;
  campaign_id: string;
  version: number;
  summary: string;
  created_at: string;
}

export interface ResearchOutputResponse {
  report: ResearchReportPublic | null;
  sources: ResearchSourcePublic[];
}

export interface VOCEvidencePublic {
  id: string;
  verbatim_quote: string | null;
  paraphrase: string | null;
  source_type: SourceType | null;
  source_locator: string | null;
  created_at: string;
}

export interface AudienceProfilePublic {
  id: string;
  campaign_id: string;
  version: number;
  summary: string;
  created_at: string;
}

export interface AudienceOutputResponse {
  profile: AudienceProfilePublic | null;
  voc_evidence: VOCEvidencePublic[];
}
