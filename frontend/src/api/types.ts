// Mirrors the FastAPI JSON schemas (app/schemas/*). Keep in sync by hand -
// the API is small and this file is the single place the shapes live.

export type SourceType =
  | "greenhouse"
  | "lever"
  | "ashby"
  | "smartrecruiters"
  | "workable"
  | "comeet"
  | "workday"
  | "taleo"
  | "jsonld"
  | "generic_html"
  | "playwright"
  | "unsupported";

export type FeedbackAction =
  | "interested"
  | "applied"
  | "not_relevant"
  | "too_senior"
  | "wrong_field"
  | "wrong_location"
  | "saved"
  | "rejected";

export type CrawlRunStatus = "running" | "success" | "partial" | "failed";

export interface Match {
  id: number;
  job_id: number;
  target_role_id: number;
  final_score: number;
  candidate_semantic_score: number;
  intent_semantic_score: number;
  skill_score: number;
  role_score: number;
  seniority_score: number;
  location_score: number;
  recency_score: number;
  reasons: string[];
  concerns: string[];
  created_at: string;
  job_title: string;
  company_id: number;
  company_name: string;
  location_text: string | null;
  country: string | null;
  source_type: SourceType;
  source_url: string;
  apply_url: string | null;
  source_published_at: string | null;
  first_seen_at: string;
  last_feedback: FeedbackAction | null;
}

export interface Job {
  id: number;
  company_id: number;
  career_source_id: number;
  external_job_id: string;
  title: string;
  department: string | null;
  location_text: string | null;
  remote_type: "onsite" | "hybrid" | "remote" | "unknown";
  employment_type: "full_time" | "part_time" | "contract" | "internship" | "temporary" | "unknown";
  source_url: string;
  apply_url: string | null;
  source_published_at: string | null;
  source_updated_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  status: "active" | "closed" | "unknown";
  description: string | null;
  responsibilities: string | null;
  qualifications: string | null;
  required_skills: string[];
  preferred_skills: string[];
}

export interface TargetRole {
  id: number;
  canonical_name: string;
  aliases: string[];
  description: string | null;
  positive_keywords: string[];
  negative_keywords: string[];
  preferred_skills: string[];
  max_expected_years: number | null;
  enabled: boolean;
}

export interface TargetRoleInput {
  canonical_name: string;
  aliases: string[];
  description?: string | null;
  positive_keywords: string[];
  negative_keywords: string[];
  preferred_skills: string[];
  max_expected_years: number | null;
  enabled: boolean;
}

export interface CandidateProfile {
  id: number;
  version: number;
  filename: string;
  file_hash: string;
  is_active: boolean;
  structured_profile: Record<string, unknown>;
  created_at: string;
  activated_at: string | null;
}

export interface SourceTypeStat {
  source_type: SourceType;
  sources: number;
  active_jobs: number;
  active_israel_jobs: number;
}

export interface RecentRun {
  company_name: string;
  source_type: SourceType;
  status: CrawlRunStatus;
  started_at: string;
  jobs_seen: number;
  jobs_created: number;
  jobs_updated: number;
  jobs_closed: number;
  jobs_failed: number;
  error_type: string | null;
}

export interface FailingSource {
  company_name: string;
  source_type: SourceType;
  source_url: string;
  consecutive_failures: number;
  last_error: string | null;
}

export interface DashboardStats {
  companies_enabled: number;
  sources_enabled: number;
  active_jobs: number;
  active_israel_or_unknown_jobs: number;
  active_israel_jobs: number;
  active_unknown_location_jobs: number;
  matches: number;
  jobs_discovered_24h: number;
  last_crawl_at: string | null;
  runs_last_hour: number;
  failed_runs_last_hour: number;
  by_source_type: SourceTypeStat[];
  recent_runs: RecentRun[];
  failing_sources: FailingSource[];
}

export interface MatchFilters {
  minScore: number;
  days: number | null;
  targetRoleId: number | null;
  israelOnly: boolean;
  hideDismissed: boolean;
  query: string;
}
