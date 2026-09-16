import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PAGE_SIZE, fetchMatches, fetchProfiles, fetchTargetRoles } from "../api/client";
import type { Match, MatchFilters } from "../api/types";
import { MatchCard } from "../components/MatchCard";
import { EmptyState, Segmented, Skeletons, Toggle } from "../components/ui";
import { useAsync } from "../hooks/useAsync";

const DEFAULT_FILTERS: MatchFilters = {
  minScore: 60,
  days: null,
  targetRoleId: null,
  israelOnly: true,
  hideDismissed: true,
  query: "",
};

export function MatchesPage() {
  const [filters, setFilters] = useState<MatchFilters>(DEFAULT_FILTERS);
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<Match[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());

  // Debounce typing so every keystroke doesn't hit the API.
  useEffect(() => {
    const handle = window.setTimeout(() => setFilters((f) => ({ ...f, query })), 250);
    return () => window.clearTimeout(handle);
  }, [query]);

  const roles = useAsync(fetchTargetRoles, []);
  const profiles = useAsync(fetchProfiles, []);
  const first = useAsync(() => fetchMatches(filters, 0), [filters]);

  useEffect(() => {
    setExtra([]);
    setExhausted(false);
  }, [filters]);

  const matches = useMemo(
    () => [...(first.data ?? []), ...extra].filter((m) => !dismissed.has(m.job_id)),
    [first.data, extra, dismissed],
  );
  const hasActiveProfile = (profiles.data ?? []).some((p) => p.is_active);

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

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">ההתאמות שלך</h1>
          <p className="page__subtitle">
            המשרות הפתוחות שהכי מתאימות לקורות החיים ולתפקידי היעד שלך, מתעדכנות כל 5 דקות.
          </p>
        </div>
      </header>

      <div className="filters" role="search">
        <input
          className="search"
          type="search"
          placeholder="חיפוש לפי שם משרה או חברה"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="חיפוש"
        />
        <Segmented
          label="ציון מינימלי"
          value={filters.minScore}
          options={[
            { value: 0, label: "הכל" },
            { value: 60, label: "60+" },
            { value: 70, label: "70+" },
            { value: 80, label: "80+" },
          ]}
          onChange={(minScore) => setFilters((f) => ({ ...f, minScore }))}
        />
        <Segmented
          label="נמצאו לאחרונה"
          value={filters.days}
          options={[
            { value: null, label: "כל הזמן" },
            { value: 1, label: "היום" },
            { value: 7, label: "השבוע" },
            { value: 30, label: "החודש" },
          ]}
          onChange={(days) => setFilters((f) => ({ ...f, days }))}
        />
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
          נסה להוריד את הציון המינימלי או להרחיב את טווח הזמן.
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
