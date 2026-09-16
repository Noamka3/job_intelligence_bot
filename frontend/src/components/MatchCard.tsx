import { useState } from "react";
import { Link } from "react-router-dom";
import type { FeedbackAction, Match } from "../api/types";
import { DISMISSING_FEEDBACK, SOURCE_LABELS, relativeTime } from "../lib/format";
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

  const posted = match.source_published_at
    ? `פורסמה ${relativeTime(match.source_published_at)}`
    : `נמצאה ${relativeTime(match.first_seen_at)}`;

  return (
    <article className={`card card--interactive match${leaving ? " card--leaving" : ""}`}>
      <ScoreBadge score={match.final_score} />
      <div>
        <h2 className="match__title">
          <Link to={`/jobs/${match.job_id}`} className="bidi">
            {match.job_title}
          </Link>
        </h2>
        <div className="match__meta">
          <span className="bidi">{match.company_name}</span>
          {match.location_text && (
            <>
              <span className="dot" />
              <span className="bidi">{match.location_text}</span>
            </>
          )}
          <span className="dot" />
          <span>{posted}</span>
          <span className="dot" />
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
          {match.apply_url && (
            <a
              className="btn btn--small btn--primary"
              href={match.apply_url}
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
