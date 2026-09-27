import { useState } from "react";
import { Link } from "react-router-dom";
import type { FeedbackAction, Match } from "../api/types";
import {
  DISMISSING_FEEDBACK,
  SENIORITY_FIT_LABELS,
  SOURCE_LABELS,
  externalHref,
  formatDate,
  relativeTime,
  jevFitDetail,
  seniorityFitDetail,
} from "../lib/format";
import { CompanyTag } from "./CompanyTag";
import { FeedbackButtons } from "./FeedbackButtons";
import { Pill, ScoreBadge } from "./ui";

export function MatchCard({
  match,
  hideDismissed,
  onDismissed,
}: {
  match: Match;
  hideDismissed: boolean;
  onDismissed: (jobId: number) => void;
}) {
  const [feedback, setFeedback] = useState<FeedbackAction | null>(match.last_feedback);
  const [leaving, setLeaving] = useState(false);

  function handleFeedback(action: FeedbackAction) {
    setFeedback(action);
    if (hideDismissed && DISMISSING_FEEDBACK.has(action)) {
      setLeaving(true);
      window.setTimeout(() => onDismissed(match.job_id), 260);
    }
  }

  const applyHref = externalHref(match.apply_url);
  const postedAt = match.source_published_at ?? match.first_seen_at;
  const posted = match.source_published_at
    ? `פורסמה ${relativeTime(postedAt)}`
    : `נמצאה ${relativeTime(postedAt)}`;

  return (
    <article className={`card card--interactive match${leaving ? " card--leaving" : ""}`}>
      <ScoreBadge score={match.final_score} />
      <div>
        <div className="match__top">
          <CompanyTag name={match.company_name} />
          <time className="match__time" dateTime={postedAt} title={formatDate(postedAt)}>
            {posted}
          </time>
        </div>
        <h2 className="match__title">
          <Link to={`/jobs/${match.job_id}`} className="bidi">
            {match.job_title}
          </Link>
        </h2>
        <div className="match__meta">
          <Pill
            tone={
              match.seniority_fit === "fit"
                ? "good"
                : match.seniority_fit === "experienced"
                  ? "bad"
                  : match.seniority_fit === "student"
                    ? "warn"
                    : "neutral"
            }
            title={seniorityFitDetail(match)}
          >
            {SENIORITY_FIT_LABELS[match.seniority_fit]}
          </Pill>
          {match.jev_fit && (
            <Pill
              tone={
                match.jev_fit.would_be_considered >= 0.7
                  ? "good"
                  : match.jev_fit.would_be_considered <= 0.3
                    ? "bad"
                    : "neutral"
              }
              title={jevFitDetail(match.jev_fit)}
            >
              Jev {Math.round(match.jev_fit.would_be_considered * 100)}%
            </Pill>
          )}
          {match.location_text && <span className="bidi">{match.location_text}</span>}
          {match.location_text && <span className="dot" />}
          <span className="faint">{SOURCE_LABELS[match.source_type]}</span>
        </div>
        {(match.reasons.length > 0 || match.concerns.length > 0) && (
          <div className="match__tags">
            {match.reasons.slice(0, 4).map((reason) => (
              <Pill key={reason} tone="good">
                {reason}
              </Pill>
            ))}
            {match.concerns.slice(0, 3).map((concern) => (
              <Pill key={concern} tone="warn">
                {concern}
              </Pill>
            ))}
          </div>
        )}
        <div className="match__actions">
          <Link to={`/jobs/${match.job_id}`} className="btn btn--small">
            פרטים
          </Link>
          {applyHref && (
            <a
              className="btn btn--small btn--primary"
              href={applyHref}
              target="_blank"
              rel="noopener noreferrer"
            >
              להגשה ↗
            </a>
          )}
          <FeedbackButtons jobId={match.job_id} current={feedback} onChange={handleFeedback} />
        </div>
      </div>
    </article>
  );
}
