import type { CrawlRunStatus, FeedbackAction, SourceType } from "../api/types";

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
  generic_html: "אתר החברה",
  playwright: "אתר החברה (דפדפן)",
  unsupported: "לא נתמך",
};

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
