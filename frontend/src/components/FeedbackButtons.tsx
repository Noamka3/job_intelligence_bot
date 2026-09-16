import { useState } from "react";
import { sendFeedback } from "../api/client";
import type { FeedbackAction } from "../api/types";
import { FEEDBACK_LABELS } from "../lib/format";

const QUICK: { action: FeedbackAction; danger?: boolean }[] = [
  { action: "interested" },
  { action: "applied" },
  { action: "not_relevant", danger: true },
  { action: "too_senior", danger: true },
];

export function FeedbackButtons({
  jobId,
  current,
  onChange,
}: {
  jobId: number;
  current: FeedbackAction | null;
  onChange?: (action: FeedbackAction) => void;
}) {
  const [pending, setPending] = useState<FeedbackAction | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(action: FeedbackAction) {
    setPending(action);
    setError(null);
    try {
      await sendFeedback(jobId, action);
      onChange?.(action);
    } catch (err) {
      setError(err instanceof Error ? err.message : "לא נשמר");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="match__feedback" role="group" aria-label="מה דעתך על המשרה">
      {QUICK.map(({ action, danger }) => (
        <button
          key={action}
          type="button"
          className={`btn btn--small${danger ? " btn--danger" : ""}`}
          aria-pressed={current === action}
          disabled={pending !== null}
          onClick={() => choose(action)}
        >
          {FEEDBACK_LABELS[action]}
        </button>
      ))}
      {error && (
        <span className="faint" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
