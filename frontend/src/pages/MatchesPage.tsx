import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  PAGE_SIZE,
  fetchMatches,
  fetchProfiles,
  fetchRegions,
  fetchStats,
  fetchTargetRoles,
} from "../api/client";
import type { Match, MatchFilters } from "../api/types";
import { MatchCard } from "../components/MatchCard";
import { EmptyState, Segmented, Skeletons, Toggle } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { relativeTime } from "../lib/format";

// How often the live line asks what the crawler found today; a change
// in that number reloads the list at once.
const LIVE_MS = 30_000;

const DEFAULT_FILTERS: MatchFilters = {
  // Today's postings that score 60 or more, newest first. Below 60 is
  // noise, and so is anything older than ten days - which is why ten is
  // the widest window offered. Nothing is deleted, only filtered.
  minScore: 60,
  days: 0,
  targetRoleId: null,
  region: null,
  israelOnly: true,
  hideDismissed: true,
  // Every level, each with its tag: the tag is enough to decide from,
  // and the narrower views are one click away.
  seniority: "all",
  query: "",
  sort: "recent",
};

export function MatchesPage() {
  const [filters, setFilters] = useState<MatchFilters>(DEFAULT_FILTERS);
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<Match[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  // Debounce typing so every keystroke doesn't hit the API (each query
  // is also embedded server-side for the semantic half of the search).
  useEffect(() => {
    const handle = window.setTimeout(() => setFilters((f) => ({ ...f, query })), 300);
    return () => window.clearTimeout(handle);
  }, [query]);

  const roles = useAsync(fetchTargetRoles, []);
  const regions = useAsync(fetchRegions, []);
  const profiles = useAsync(fetchProfiles, []);
  const first = useAsync(() => fetchMatches(filters, 0), [filters]);
  const stats = useAsync(fetchStats, []);

  useEffect(() => {
    setExtra([]);
    setExhausted(false);
  }, [filters]);

  useEffect(() => {
    const handle = window.setInterval(stats.reload, LIVE_MS);
    return () => window.clearInterval(handle);
  }, [stats.reload]);

  const foundToday = stats.data?.jobs_found_today;
  const lastFoundToday = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (foundToday === undefined) return;
    if (lastFoundToday.current !== undefined && lastFoundToday.current !== foundToday) {
      first.reload();
    }
    lastFoundToday.current = foundToday;
  }, [foundToday, first.reload]);

  const matches = useMemo(
    () => [...(first.data ?? []), ...extra].filter((m) => !dismissed.has(m.job_id)),
    [first.data, extra, dismissed],
  );
  const hasActiveProfile = (profiles.data ?? []).some((p) => p.is_active);
  const searching = filters.query.trim().length > 0;

  async function loadMore() {
    setLoadingMore(true);
    try {
      const page = await fetchMatches(filters, (first.data?.length ?? 0) + extra.length);
      setExtra((e) => [...e, ...page]);
      if (page.length < PAGE_SIZE) setExhausted(true);
    } finally {
      setLoadingMore(false);
    }
  }

  const subtitle = searching
    ? "התאמות מדויקות לכותרת/חברה קודם, ואחריהן משרות שקרובות במשמעות למה שכתבת."
    : filters.sort === "recent"
      ? filters.days === null
        ? "המשרות החדשות ביותר קודם."
        : `מה שנמצא או פורסם ${filters.days === 0 ? "היום" : `ב-${filters.days} הימים האחרונים`}, החדשות קודם.`
      : "המשרות שהכי מתאימות לקורות החיים ולתפקידי היעד שלך, קודם.";

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">ההתאמות שלך</h1>
          <p className="page__subtitle">{subtitle}</p>
          {stats.data && (
            <p className="page__subtitle" aria-live="polite">
              היום נמצאו {stats.data.jobs_found_today} משרות חדשות, {stats.data.relevant_today} מהן
              מעל 60% התאמה
              {stats.data.last_crawl_at &&
                ` · סריקה אחרונה ${relativeTime(stats.data.last_crawl_at)}`}
              . הרשימה מתעדכנת לבד ברגע שנמצא משהו חדש.
            </p>
          )}
        </div>
      </header>

      <div className="filters" role="search">
        <input
          className="search"
          type="search"
          placeholder="חפש לפי שם, חברה - או תאר במילים שלך מה אתה מחפש"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="חיפוש"
        />
        {!searching && (
          <Segmented
            label="מיון"
            value={filters.sort}
            options={[
              { value: "recent", label: "חדשות קודם" },
              { value: "score", label: "התאמה קודם" },
            ]}
            onChange={(sort) => setFilters((f) => ({ ...f, sort }))}
          />
        )}
        <Segmented
          label="התאמה מינימלית"
          value={filters.minScore}
          options={[
            { value: 60, label: "60%+" },
            { value: 70, label: "70%+" },
            { value: 80, label: "80%+" },
          ]}
          onChange={(minScore) => setFilters((f) => ({ ...f, minScore }))}
        />
        <Segmented
          label="ותק"
          value={filters.seniority}
          options={[
            { value: "fit", label: "רק מתאים לג'וניור" },
            { value: "not_experienced", label: "בלי דורשות ניסיון" },
            { value: "student", label: "משרות סטודנט" },
            { value: "all", label: "הכל" },
          ]}
          onChange={(seniority) => setFilters((f) => ({ ...f, seniority }))}
        />
        <Segmented
          label="נמצאו לאחרונה"
          value={filters.days}
          options={[
            { value: 0, label: "היום" },
            { value: 3, label: "3 ימים" },
            { value: 10, label: "10 ימים" },
          ]}
          onChange={(days) => setFilters((f) => ({ ...f, days }))}
        />
        <select
          className="select"
          aria-label="אזור"
          value={filters.region ?? ""}
          onChange={(e) => setFilters((f) => ({ ...f, region: e.target.value || null }))}
        >
          <option value="">כל הארץ</option>
          {Object.entries(regions.data ?? {}).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        {(roles.data?.length ?? 0) > 1 && (
          <select
            className="select"
            aria-label="תפקיד יעד"
            value={filters.targetRoleId ?? ""}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                targetRoleId: e.target.value ? Number(e.target.value) : null,
              }))
            }
          >
            <option value="">כל התפקידים</option>
            {roles.data?.map((role) => (
              <option key={role.id} value={role.id}>
                {role.canonical_name}
              </option>
            ))}
          </select>
        )}
        <Toggle
          label="ישראל בלבד"
          checked={filters.israelOnly}
          onChange={(israelOnly) => setFilters((f) => ({ ...f, israelOnly }))}
        />
        <Toggle
          label="הסתר מה שדחיתי"
          checked={filters.hideDismissed}
          onChange={(hideDismissed) => setFilters((f) => ({ ...f, hideDismissed }))}
        />
      </div>

      {first.error && (
        <div className="alert" role="alert">
          {first.error}
        </div>
      )}

      {first.loading && !first.data ? (
        <Skeletons />
      ) : !hasActiveProfile && profiles.data ? (
        <EmptyState
          title="עוד אין קורות חיים במערכת"
          action={
            <Link to="/profile" className="btn btn--primary">
              להעלות קורות חיים
            </Link>
          }
        >
          ההתאמות מחושבות מול קורות החיים שלך - זה השלב הראשון.
        </EmptyState>
      ) : matches.length === 0 ? (
        <EmptyState title="אין התאמות בפילטרים האלה">
          נסה להוריד את סף ההתאמה, להרחיב את טווח הזמן או לבחור "כל הארץ".
        </EmptyState>
      ) : (
        <div className="stack" aria-live="polite">
          {matches.map((match) => (
            <MatchCard
              key={match.id}
              match={match}
              hideDismissed={filters.hideDismissed}
              onDismissed={(jobId) => setDismissed((d) => new Set(d).add(jobId))}
            />
          ))}
          {!exhausted && (first.data?.length ?? 0) >= PAGE_SIZE && (
            <div style={{ textAlign: "center" }}>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={loadMore}
                disabled={loadingMore}
              >
                {loadingMore ? "טוען…" : "עוד משרות"}
              </button>
            </div>
          )}
        </div>
      )}
    </main>
  );
}
