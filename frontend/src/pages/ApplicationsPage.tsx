import { useState } from "react";
import { Link } from "react-router-dom";
import { fetchApplications, updateApplication } from "../api/client";
import type { Application, ApplicationStatus } from "../api/types";
import { CompanyTag } from "../components/CompanyTag";
import { EmptyState, Pill, Segmented, Skeletons } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import {
  APPLICATION_STATUS_LABELS,
  APPLICATION_STATUS_ORDER,
  CLOSED_APPLICATION_STATUSES,
  formatDate,
  relativeTime,
} from "../lib/format";

type View = "open" | "closed" | "all";

export function ApplicationsPage() {
  const applications = useAsync(fetchApplications, []);
  const [view, setView] = useState<View>("open");

  const all = applications.data ?? [];
  const counts = APPLICATION_STATUS_ORDER.map((status) => ({
    status,
    count: all.filter((a) => a.status === status).length,
  })).filter((c) => c.count > 0);
  const shown = all.filter((a) =>
    view === "all"
      ? true
      : view === "closed"
        ? CLOSED_APPLICATION_STATUSES.has(a.status)
        : !CLOSED_APPLICATION_STATUSES.has(a.status),
  );

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">ההגשות שלי</h1>
          <p className="page__subtitle">
            כל משרה שסימנת "הגשתי" מגיעה לכאן. עדכן איפה התהליך עומד ורשום לעצמך הערות -
            שום דבר לא נמחק.
          </p>
        </div>
        <Segmented
          label="תצוגה"
          value={view}
          options={[
            { value: "open", label: "בתהליך" },
            { value: "closed", label: "הסתיימו" },
            { value: "all", label: "הכל" },
          ]}
          onChange={setView}
        />
      </header>

      {counts.length > 0 && (
        <div className="row" style={{ marginBottom: 18 }}>
          {counts.map(({ status, count }) => (
            <Pill
              key={status}
              tone={
                status === "offer"
                  ? "good"
                  : CLOSED_APPLICATION_STATUSES.has(status)
                    ? "neutral"
                    : "accent"
              }
            >
              {APPLICATION_STATUS_LABELS[status]} · {count}
            </Pill>
          ))}
        </div>
      )}

      {applications.loading && !applications.data ? (
        <Skeletons count={2} />
      ) : applications.error ? (
        <div className="alert" role="alert">
          {applications.error}
        </div>
      ) : all.length === 0 ? (
        <EmptyState
          title="עוד לא סימנת שום הגשה"
          action={
            <Link to="/" className="btn btn--primary">
              להתאמות
            </Link>
          }
        >
          לחיצה על "הגשתי" בכרטיס משרה פותחת אותה כאן אוטומטית.
        </EmptyState>
      ) : shown.length === 0 ? (
        <EmptyState title="אין הגשות בתצוגה הזו" />
      ) : (
        <div className="stack">
          {shown.map((application) => (
            <ApplicationCard
              key={application.id}
              application={application}
              onChanged={applications.reload}
            />
          ))}
        </div>
      )}
    </main>
  );
}

function ApplicationCard({
  application,
  onChanged,
}: {
  application: Application;
  onChanged: () => void;
}) {
  const [status, setStatus] = useState<ApplicationStatus>(application.status);
  const [notes, setNotes] = useState(application.notes ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(patch: { status?: ApplicationStatus; notes?: string | null }) {
    setSaving(true);
    setError(null);
    try {
      await updateApplication(application.job_id, patch);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "לא נשמר");
    } finally {
      setSaving(false);
    }
  }

  return (
    <article className="card app-row">
      <div>
        <div className="match__top">
          <CompanyTag name={application.company_name} />
          <span className="match__time" title={formatDate(application.applied_at)}>
            הגשת {relativeTime(application.applied_at)}
          </span>
        </div>
        <h2 className="match__title">
          <Link to={`/jobs/${application.job_id}`} className="bidi">
            {application.job_title}
          </Link>
        </h2>
        <div className="match__meta">
          {application.location_text && (
            <span className="bidi">{application.location_text}</span>
          )}
          {application.job_status !== "active" && (
            <>
              <span className="dot" />
              <Pill tone="warn">המשרה כבר לא מפורסמת</Pill>
            </>
          )}
        </div>
        <textarea
          className="notes"
          style={{ marginTop: 12 }}
          placeholder="הערות לעצמך: עם מי דיברת, מה נשאל, מתי חוזרים אליך…"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={() => {
            if ((notes || "") !== (application.notes ?? "")) void save({ notes: notes || null });
          }}
          aria-label="הערות"
        />
        {error && (
          <div className="faint" role="alert">
            {error}
          </div>
        )}
      </div>
      <div className="stack" style={{ gap: 8, minWidth: 170 }}>
        <label className="field">
          שלב בתהליך
          <select
            className="status-select"
            value={status}
            disabled={saving}
            onChange={(e) => {
              const next = e.target.value as ApplicationStatus;
              setStatus(next);
              void save({ status: next });
            }}
          >
            {APPLICATION_STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {APPLICATION_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <span className="faint">עודכן {relativeTime(application.updated_at)}</span>
        <a
          className="btn btn--small btn--ghost"
          href={application.apply_url ?? application.source_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          למשרה באתר ↗
        </a>
      </div>
    </article>
  );
}
