import type { ReactNode } from "react";
import { scoreTone } from "../lib/format";

export function ScoreBadge({ score, large = false }: { score: number; large?: boolean }) {
  const tone = scoreTone(score);
  return (
    <div
      className={`score score--${tone}${large ? " score--large" : ""}`}
      role="img"
      aria-label={`ציון התאמה ${Math.round(score)} מתוך 100`}
    >
      {Math.round(score)}
    </div>
  );
}

export function Pill({
  children,
  tone = "neutral",
  title,
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "bad" | "accent";
  title?: string;
}) {
  const cls = tone === "neutral" ? "pill" : `pill pill--${tone}`;
  return (
    <span className={cls} title={title}>
      <span className="pill__text bidi">{children}</span>
    </span>
  );
}

export function Segmented<T extends string | number | null>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className="segmented__item"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="card empty">
      <p className="empty__title">{title}</p>
      {children && <p style={{ margin: 0 }}>{children}</p>}
      {action && <div style={{ marginTop: 16 }}>{action}</div>}
    </div>
  );
}

export function StatTile({
  value,
  label,
  tone,
}: {
  value: ReactNode;
  label: string;
  tone?: "accent" | "good";
}) {
  return (
    <div className={`tile${tone ? ` tile--${tone}` : ""}`}>
      <div className="tile__value">{value}</div>
      <div className="tile__label">{label}</div>
    </div>
  );
}

export function Skeletons({ count = 4 }: { count?: number }) {
  return (
    <div className="stack" aria-busy="true" aria-label="טוען">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton" />
      ))}
    </div>
  );
}
