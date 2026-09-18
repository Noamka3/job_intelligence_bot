import type {
  ApplicationStatus,
  CrawlRunStatus,
  FeedbackAction,
  Match,
  SeniorityFit,
  SourceType,
} from "../api/types";

const relative = new Intl.RelativeTimeFormat("he", { numeric: "auto" });
const dateTime = new Intl.DateTimeFormat("he-IL", { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("he-IL", { dateStyle: "medium" });

/** "לפני 3 שעות" / "אתמול" / "לפני חודש" - what a person wants to read. */
export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const diffSeconds = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diffSeconds);
  if (abs < 60) return "עכשיו";
  if (abs < 3600) return relative.format(Math.round(diffSeconds / 60), "minute");
  if (abs < 86400) return relative.format(Math.round(diffSeconds / 3600), "hour");
  if (abs < 86400 * 30) return relative.format(Math.round(diffSeconds / 86400), "day");
  if (abs < 86400 * 365) return relative.format(Math.round(diffSeconds / (86400 * 30)), "month");
  return relative.format(Math.round(diffSeconds / (86400 * 365)), "year");
}

export const formatDateTime = (iso: string | null | undefined) =>
  iso ? dateTime.format(new Date(iso)) : "";
export const formatDate = (iso: string | null | undefined) =>
  iso ? dateOnly.format(new Date(iso)) : "";

export type ScoreTone = "great" | "good" | "fair" | "low";

export function scoreTone(score: number): ScoreTone {
  if (score >= 75) return "great";
  if (score >= 65) return "good";
  if (score >= 50) return "fair";
  return "low";
}

export const SOURCE_LABELS: Record<SourceType, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  smartrecruiters: "SmartRecruiters",
  workable: "Workable",
  comeet: "Comeet",
  workday: "Workday",
  taleo: "Taleo",
  jsonld: "אתר החברה",
  site_feed: "הפיד של החברה",
  wordpress: "אתר החברה (WordPress)",
  generic_html: "אתר החברה",
  playwright: "אתר החברה (דפדפן)",
  unsupported: "לא נתמך",
};

export const SENIORITY_FIT_LABELS: Record<SeniorityFit, string> = {
  fit: "מתאים לג'וניור",
  unknown: "ותק לא צוין",
  experienced: "דורש ניסיון",
};

/** The tag's one-line explanation: what in the posting decided it. */
export function seniorityFitDetail(match: Match): string {
  if (match.seniority_fit === "experienced" && match.experience_min_years !== null) {
    return `דורש ${match.experience_min_years}+ שנות ניסיון`;
  }
  if (match.seniority_fit === "experienced") return `כותרת ברמת ${match.job_seniority}`;
  if (match.seniority_fit === "fit" && match.experience_min_years !== null) {
    return match.experience_min_years === 0
      ? "לא נדרש ניסיון"
      : `עד ${match.experience_min_years} שנות ניסיון`;
  }
  if (match.seniority_fit === "fit") return "כותרת של משרת התחלה";
  return "המשרה לא מציינת דרישת ניסיון";
}

export const FEEDBACK_LABELS: Record<FeedbackAction, string> = {
  interested: "מעניין אותי",
  applied: "הגשתי",
  saved: "שמור",
  not_relevant: "לא רלוונטי",
  too_senior: "בכיר מדי",
  wrong_field: "תחום אחר",
  wrong_location: "מיקום לא מתאים",
  rejected: "נדחה",
};

export const DISMISSING_FEEDBACK: ReadonlySet<FeedbackAction> = new Set([
  "not_relevant",
  "too_senior",
  "wrong_field",
  "wrong_location",
  "rejected",
]);

export const APPLICATION_STATUS_LABELS: Record<ApplicationStatus, string> = {
  applied: "הגשתי",
  screening: "סינון טלפוני",
  interview: "ראיון",
  assignment: "מטלת בית",
  offer: "הצעה",
  rejected: "נדחיתי",
  withdrawn: "פרשתי",
};

/** Pipeline order - what the applications page walks through. */
export const APPLICATION_STATUS_ORDER: ApplicationStatus[] = [
  "applied",
  "screening",
  "interview",
  "assignment",
  "offer",
  "rejected",
  "withdrawn",
];

export const CLOSED_APPLICATION_STATUSES: ReadonlySet<ApplicationStatus> = new Set([
  "rejected",
  "withdrawn",
]);

/** "mm:ss" until a moment, or "" once it has passed. */
export function countdown(iso: string | null, now: number): string {
  if (!iso) return "";
  const remaining = Math.max(0, Math.round((new Date(iso).getTime() - now) / 1000));
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export const RUN_STATUS_LABELS: Record<CrawlRunStatus, string> = {
  running: "רץ",
  success: "הצליח",
  partial: "חלקי",
  failed: "נכשל",
};

export const COMPONENT_LABELS: { key: string; label: string; hint: string }[] = [
  { key: "seniority_score", label: "רמת ותק", hint: "האם המשרה מתאימה לרמה שאתה מחפש" },
  { key: "skill_score", label: "כישורים", hint: "כמה מהכישורים שהמשרה דורשת יש לך" },
  { key: "role_score", label: "התאמת תפקיד", hint: "התאמת הכותרת לתפקיד היעד" },
  { key: "candidate_semantic_score", label: "דמיון לקורות החיים", hint: "דמיון סמנטי למה שכתוב בקו״ח" },
  { key: "intent_semantic_score", label: "דמיון לתפקיד היעד", hint: "דמיון סמנטי להגדרת התפקיד שלך" },
  { key: "location_score", label: "מיקום", hint: "ישראל / היברידי / מרוחק" },
  { key: "recency_score", label: "טריות", hint: "כמה זמן המשרה מפורסמת" },
];

export function extractedSkills(structured: Record<string, unknown>): string[] {
  const keys = ["skills", "programming_languages", "frameworks", "databases", "cloud", "devops"];
  const seen = new Set<string>();
  for (const key of keys) {
    const values = structured[key];
    if (Array.isArray(values)) for (const v of values) if (typeof v === "string") seen.add(v);
  }
  return [...seen];
}

/** Every section of the structured CV profile, in display order, with a
 * Hebrew label - so the user can check what the scan understood. */
export const PROFILE_SECTIONS: { key: string; label: string }[] = [
  { key: "programming_languages", label: "שפות תכנות" },
  { key: "frameworks", label: "פריימוורקים וספריות" },
  { key: "databases", label: "בסיסי נתונים" },
  { key: "cloud", label: "ענן" },
  { key: "devops", label: "DevOps וכלים" },
  { key: "skills", label: "כישורים נוספים" },
  { key: "domains", label: "תחומים" },
  { key: "education", label: "השכלה" },
  { key: "projects", label: "פרויקטים" },
  { key: "keywords", label: "מילות מפתח" },
];

export function stringList(structured: Record<string, unknown>, key: string): string[] {
  const values = structured[key];
  return Array.isArray(values) ? values.filter((v): v is string => typeof v === "string") : [];
}
