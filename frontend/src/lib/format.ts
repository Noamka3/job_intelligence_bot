import type {
  JevFit,
  JevReading,
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

/** A URL we are willing to put in an href, or null. Job links are read
 * off third-party career pages, and a "javascript:" one would run in this
 * dashboard's own origin; only http(s) is ever linked. */
export function externalHref(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
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
  student: "משרת סטודנט",
};

/** The tag's one-line explanation: what in the posting decided it. */
export function seniorityFitDetail(match: Match): string {
  if (match.seniority_fit === "student") return "פתוחה לסטודנטים בלבד (התמחות / משרת סטודנט)";
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

export const JEV_EXPERIENCE_LABELS: Record<JevFit["experience_level"], string> = {
  below: "דורשת יותר ניסיון ממה שיש לך",
  matches: "רמת הניסיון מתאימה",
  above: "יש לך יותר ניסיון ממה שצריך",
};

export const JEV_ROLE_FAMILY_LABELS: Record<string, string> = {
  software_development: "פיתוח תוכנה",
  qa_automation: "QA / אוטומציה",
  data: "דאטה",
  devops_it: "DevOps / IT",
  hardware: "חומרה",
  product_management: "מוצר / פרויקטים",
  other: "תחום אחר",
};

export const JEV_SENIORITY_LABELS: Record<JevReading["seniority"], string> = {
  student: "משרת סטודנט",
  junior: "ג'וניור",
  mid: "2–4 שנים",
  senior: "סניור",
  lead: "ניהול",
};

/** Jev's reading of a posting in one line, next to the rule-based tag. */
export function jevReadingSummary(r: JevReading): string {
  const family = JEV_ROLE_FAMILY_LABELS[r.role_family] ?? r.role_family;
  const level = `${JEV_SENIORITY_LABELS[r.seniority]} (${Math.round(r.seniority_confidence * 100)}%)`;
  const students = r.students_only >= 0.5 ? "רק לסטודנטים" : "לא רק לסטודנטים";
  const experience = r.experience_required >= 0.5 ? "דורש ניסיון" : "לא דורש ניסיון";
  return `${family} · ${level} · ${students} · ${experience}`;
}

/** Jev's three answers in one line, for the card's tooltip. */
export function jevFitDetail(fit: JevFit): string {
  const coverage = Math.round(fit.skills_coverage * 10) / 10;
  return `מגייס היה שוקל אותך: ${Math.round(fit.would_be_considered * 100)}% · כיסוי כישורים ${coverage}/4 · ${JEV_EXPERIENCE_LABELS[fit.experience_level]}`;
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

/** Every section of the structured CV profile, in display order, with a
 * Hebrew label - so a missed skill is visible, not silently absent. */
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
