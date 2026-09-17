import type {
  Application,
  ApplicationStatus,
  CandidateProfile,
  CandidateProfileText,
  DashboardStats,
  FeedbackAction,
  Job,
  Match,
  MatchFilters,
  TargetRole,
  TargetRoleInput,
} from "./types";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = "קלט לא תקין";
    } catch {
      // non-JSON error body - keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const PAGE_SIZE = 30;

export function fetchMatches(filters: MatchFilters, offset: number): Promise<Match[]> {
  const params = new URLSearchParams({
    min_score: String(filters.minScore),
    israel_only: String(filters.israelOnly),
    hide_dismissed: String(filters.hideDismissed),
    sort: filters.sort,
    limit: String(PAGE_SIZE),
    offset: String(offset),
  });
  if (filters.days !== null) params.set("days", String(filters.days));
  if (filters.targetRoleId !== null) params.set("target_role_id", String(filters.targetRoleId));
  if (filters.region !== null) params.set("region", filters.region);
  if (filters.query.trim()) params.set("q", filters.query.trim());
  return request<Match[]>(`/matches/top?${params.toString()}`);
}

export const fetchRegions = () => request<Record<string, string>>("/matches/regions");
export const fetchJob = (id: number) => request<Job>(`/jobs/${id}`);
export const fetchMatchForJob = (jobId: number) => request<Match>(`/matches/job/${jobId}`);
export const sendFeedback = (jobId: number, action: FeedbackAction) =>
  request<unknown>(`/jobs/${jobId}/feedback`, json("POST", { action }));

export const fetchStats = () => request<DashboardStats>("/dashboard/stats");

export const fetchApplications = () => request<Application[]>("/applications");
export const updateApplication = (
  jobId: number,
  patch: { status?: ApplicationStatus; notes?: string | null },
) => request<Application>(`/applications/${jobId}`, json("PATCH", patch));

export const fetchTargetRoles = () => request<TargetRole[]>("/target-roles");
export const createTargetRole = (input: TargetRoleInput) =>
  request<TargetRole>("/target-roles", json("POST", input));
export const updateTargetRole = (id: number, patch: Partial<TargetRoleInput>) =>
  request<TargetRole>(`/target-roles/${id}`, json("PATCH", patch));

export const fetchProfiles = () => request<CandidateProfile[]>("/candidate/profiles");
export const fetchProfileText = (id: number) =>
  request<CandidateProfileText>(`/candidate/profiles/${id}/text`);
export const activateProfile = (id: number) =>
  request<CandidateProfile>(`/candidate/profiles/${id}/activate`, { method: "POST" });
export function uploadResume(file: File): Promise<CandidateProfile> {
  const form = new FormData();
  form.append("file", file);
  return request<CandidateProfile>("/candidate/resume", { method: "POST", body: form });
}
