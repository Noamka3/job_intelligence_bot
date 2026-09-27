import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, fetchJob, fetchMatchForJob } from "../api/client";
import type { FeedbackAction, Match } from "../api/types";
import { CompanyTag } from "../components/CompanyTag";
import { FeedbackButtons } from "../components/FeedbackButtons";
import { Pill, ScoreBadge, Skeletons } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import {
  COMPONENT_LABELS,
  JEV_EXPERIENCE_LABELS,
  jevReadingSummary,
  SOURCE_LABELS,
  externalHref,
  formatDate,
  relativeTime,
  scoreTone,
} from "../lib/format";

const EMPLOYMENT: Record<string, string> = {
  full_time: "משרה מלאה",
  part_time: "משרה חלקית",
  contract: "חוזה",
  internship: "התמחות",
  temporary: "זמני",
};
const REMOTE: Record<string, string> = { remote: "מרחוק", hybrid: "היברידי", onsite: "במשרד" };

export function JobPage() {
  const { id } = useParams();
  const jobId = Number(id);
  const job = useAsync(() => fetchJob(jobId), [jobId]);
  const match = useAsync(
    () =>
      fetchMatchForJob(jobId).catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 404) return null;
        throw err;
      }),
    [jobId],
  );
  const [feedback, setFeedback] = useState<FeedbackAction | null | undefined>(undefined);

  if (job.loading && !job.data) {
    return (
      <main id="main" className="page">
        <Skeletons count={2} />
      </main>
    );
  }
  if (job.error || !job.data) {
    return (
      <main id="main" className="page">
        <div className="alert" role="alert">
          {job.error ?? "המשרה לא נמצאה"}
        </div>
      </main>
    );
  }
  const j = job.data;
  const m: Match | null = match.data ?? null;
  const currentFeedback = feedback === undefined ? (m?.last_feedback ?? null) : feedback;

  return (
    <main id="main" className="page">
      <p>
        <Link to="/">‹ חזרה להתאמות</Link>
      </p>
      <header className="page__header">
        <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
          {m && <ScoreBadge score={m.final_score} large />}
          <div>
            {m && (
              <div style={{ marginBottom: 10 }}>
                <CompanyTag name={m.company_name} large />
              </div>
            )}
            <h1 className="page__title bidi">{j.title}</h1>
            <p className="page__subtitle">
              {j.location_text && <span className="bidi">{j.location_text}</span>}
              {j.location_text && j.department && " · "}
              {j.department && <span className="bidi">{j.department}</span>}
            </p>
            <div className="row" style={{ marginTop: 10 }}>
              {j.employment_type !== "unknown" && <Pill>{EMPLOYMENT[j.employment_type]}</Pill>}
              {j.remote_type !== "unknown" && <Pill>{REMOTE[j.remote_type]}</Pill>}
              {j.status !== "active" && <Pill tone="bad">המשרה כבר לא מפורסמת</Pill>}
              <Pill>{SOURCE_LABELS[m?.source_type ?? "generic_html"]}</Pill>
              {currentFeedback === "applied" && (
                <Link to="/applications" className="pill pill--accent">
                  הגשת - לעדכון התהליך
                </Link>
              )}
            </div>
          </div>
        </div>
        <div className="row">
          {externalHref(j.apply_url) && (
            <a
              className="btn btn--primary"
              href={externalHref(j.apply_url) ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
            >
              להגשה ↗
            </a>
          )}
          {externalHref(j.source_url) && (
            <a
              className="btn btn--ghost"
              href={externalHref(j.source_url) ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
            >
              למקור
            </a>
          )}
        </div>
      </header>

      <div className="row faint" style={{ marginBottom: 20 }}>
        {j.source_published_at && (
          <span>
            פורסמה {formatDate(j.source_published_at)} ({relativeTime(j.source_published_at)})
          </span>
        )}
        <span>נמצאה על ידי הבוט {relativeTime(j.first_seen_at)}</span>
        <span>נראתה לאחרונה {relativeTime(j.last_seen_at)}</span>
      </div>
      {j.jev_reading && (
        <div className="row faint" style={{ marginBottom: 20 }} title={j.jev_reading.model}>
          <span>Jev קורא את המשרה כך: {jevReadingSummary(j.jev_reading)}</span>
        </div>
      )}

      {m && (
        <section className="card">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: 14 }}>
            <h2 className="section-title" style={{ margin: 0 }}>
              למה {Math.round(m.final_score)}% התאמה
            </h2>
            <FeedbackButtons jobId={j.id} current={currentFeedback} onChange={setFeedback} />
          </div>
          <div className="bars">
            {COMPONENT_LABELS.map(({ key, label, hint }) => {
              const value = (m as unknown as Record<string, number>)[key] ?? 0;
              const pct = Math.round(value * 100);
              const tone = scoreTone(pct);
              return (
                <div className="bar" key={key} title={hint}>
                  <span>{label}</span>
                  <div className="bar__track" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
                    <div className={`bar__fill bar__fill--${tone}`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="bar__value">{pct}%</span>
                </div>
              );
            })}
          </div>
          {(m.reasons.length > 0 || m.concerns.length > 0) && (
            <div className="match__tags" style={{ marginTop: 16 }}>
              {m.reasons.map((reason) => (
                <Pill key={reason} tone="good">
                  {reason}
                </Pill>
              ))}
              {m.concerns.map((concern) => (
                <Pill key={concern} tone="warn">
                  {concern}
                </Pill>
              ))}
            </div>
          )}
          {m.jev_fit && (
            <div className="muted" style={{ marginTop: 16 }}>
              <strong>דעה שנייה (Jev, {m.jev_fit.model}):</strong> מגייס היה שוקל אותך 
              {Math.round(m.jev_fit.would_be_considered * 100)}% · כיסוי כישורים 
              {Math.round(m.jev_fit.skills_coverage * 10) / 10}/4 ·{" "}
              {JEV_EXPERIENCE_LABELS[m.jev_fit.experience_level]}. לא משפיע על הציון.
            </div>
          )}
        </section>
      )}

      {(j.required_skills.length > 0 || j.preferred_skills.length > 0) && (
        <>
          <h2 className="section-title">כישורים</h2>
          <div className="match__tags" style={{ marginTop: 0 }}>
            {j.required_skills.map((s) => (
              <Pill key={`r-${s}`} tone="accent">
                {s}
              </Pill>
            ))}
            {j.preferred_skills.map((s) => (
              <Pill key={`p-${s}`}>{s}</Pill>
            ))}
          </div>
        </>
      )}

      {j.responsibilities && (
        <>
          <h2 className="section-title">תחומי אחריות</h2>
          <div className="card prose bidi">{j.responsibilities}</div>
        </>
      )}
      {j.qualifications && (
        <>
          <h2 className="section-title">דרישות</h2>
          <div className="card prose bidi">{j.qualifications}</div>
        </>
      )}
      <h2 className="section-title">תיאור המשרה</h2>
      {j.description ? (
        <div className="card prose bidi">{j.description}</div>
      ) : (
        <div className="card muted">
          אין תיאור זמין - האתר של החברה מציג אותו רק בדפדפן. אפשר לראות אותו במקור.
        </div>
      )}
    </main>
  );
}
