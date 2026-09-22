import { useEffect, useState } from "react";
import { fetchStats } from "../api/client";
import { Pill, Skeletons, StatTile } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import {
  RUN_STATUS_LABELS,
  SOURCE_LABELS,
  countdown,
  formatDateTime,
  relativeTime,
} from "../lib/format";

const REFRESH_MS = 30_000;

export function StatusPage() {
  const stats = useAsync(fetchStats, []);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const handle = window.setInterval(stats.reload, REFRESH_MS);
    return () => window.clearInterval(handle);
  }, [stats.reload]);

  // The countdown ticks every second; the data behind it refreshes every
  // 30s, so the moment the scheduler fires the clock resets by itself.
  useEffect(() => {
    const handle = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(handle);
  }, []);

  if (stats.loading && !stats.data) {
    return (
      <main id="main" className="page">
        <Skeletons count={3} />
      </main>
    );
  }
  if (stats.error || !stats.data) {
    return (
      <main id="main" className="page">
        <div className="alert" role="alert">
          {stats.error ?? "לא ניתן לטעון"}
        </div>
      </main>
    );
  }
  const s = stats.data;
  const nextAt = s.next_dispatch_at ? new Date(s.next_dispatch_at).getTime() : null;
  // Two minutes past the expected tick with no new tick = the scheduler
  // isn't running (Beat/worker down), not just a slow crawl.
  const late = nextAt !== null && now - nextAt > 2 * 60_000;
  const alive = s.last_dispatch_at !== null && !late;

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">מצב המערכת</h1>
          <p className="page__subtitle">
            <span className={`pulse${alive ? "" : " pulse--stale"}`} aria-hidden="true" />{" "}
            {s.last_dispatch_at
              ? `המתזמן רץ ${relativeTime(s.last_dispatch_at)}`
              : "המתזמן עוד לא רץ"}
            {s.last_crawl_at && ` · סריקה אחרונה ${relativeTime(s.last_crawl_at)}`}
            {late && " - מאחר: בדוק שה-worker ו-beat פעילים"}
          </p>
        </div>
      </header>

      <section className="card heartbeat" style={{ marginBottom: 18 }}>
        <div>
          <div className={`countdown${late ? " countdown--late" : ""}`} aria-live="off">
            {nextAt === null ? "--:--" : late ? "מאחר" : countdown(s.next_dispatch_at, now)}
          </div>
          <div className="faint" style={{ marginTop: 4 }}>
            עד הרענון הבא של המשרות
          </div>
        </div>
        <div className="muted" style={{ maxWidth: 520 }}>
          הבוט בודק כל <strong>{s.poll_interval_minutes} דקות</strong> אילו מקורות הגיע זמנם,
          ומכניס אותם לתור הסריקה. משרה חדשה מופיעה ב"התאמות" מיד אחרי שהסריקה שלה מסתיימת.
          {s.crawl_queue_depth !== null && (
            <>
              {" "}
              כרגע ממתינים בתור: <strong>{s.crawl_queue_depth}</strong> מקורות.
            </>
          )}
          {s.last_dispatch_at && (
            <>
              {" "}
              ריצה אחרונה: {formatDateTime(s.last_dispatch_at)}.
            </>
          )}
        </div>
      </section>

      <div className="tiles">
        <StatTile value={s.active_israel_jobs} label="משרות פעילות בישראל" tone="good" />
        <StatTile value={s.active_unknown_location_jobs} label="משרות ללא מיקום ידוע" />
        <StatTile value={s.active_jobs} label="משרות פעילות סה״כ" />
        <StatTile value={s.jobs_found_today} label="נמצאו היום" tone="accent" />
        <StatTile value={s.jobs_discovered_24h} label="נמצאו ב-24 השעות האחרונות" tone="accent" />
        <StatTile value={s.companies_enabled} label="חברות במעקב" />
        <StatTile value={s.sources_enabled} label="מקורות פעילים" />
        <StatTile value={s.matches} label="התאמות מחושבות" />
        <StatTile
          value={`${s.runs_last_hour - s.failed_runs_last_hour}/${s.runs_last_hour}`}
          label="סריקות מוצלחות בשעה האחרונה"
          tone={s.failed_runs_last_hour === 0 ? "good" : undefined}
        />
      </div>

      <h2 className="section-title">לפי סוג מקור</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>מקור</th>
              <th>מקורות</th>
              <th>משרות פעילות</th>
              <th>מהן בישראל</th>
            </tr>
          </thead>
          <tbody>
            {s.by_source_type.map((row) => (
              <tr key={row.source_type}>
                <td>{SOURCE_LABELS[row.source_type]}</td>
                <td className="num">{row.sources}</td>
                <td className="num">{row.active_jobs}</td>
                <td className="num">{row.active_israel_jobs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {s.failing_sources.length > 0 && (
        <>
          <h2 className="section-title">מקורות שנכשלים</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>חברה</th>
                  <th>סוג</th>
                  <th>כשלונות רצופים</th>
                  <th>שגיאה אחרונה</th>
                </tr>
              </thead>
              <tbody>
                {s.failing_sources.map((row) => (
                  <tr key={row.source_url}>
                    <td className="bidi">
                      <a href={row.source_url} target="_blank" rel="noopener noreferrer">
                        {row.company_name}
                      </a>
                    </td>
                    <td>{SOURCE_LABELS[row.source_type]}</td>
                    <td className="num">{row.consecutive_failures}</td>
                    <td className="faint">{row.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2 className="section-title">סריקות אחרונות</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>מתי</th>
              <th>חברה</th>
              <th>מקור</th>
              <th>מצב</th>
              <th>נראו</th>
              <th>חדשות</th>
              <th>עודכנו</th>
              <th>נסגרו</th>
              <th>דולגו</th>
            </tr>
          </thead>
          <tbody>
            {s.recent_runs.map((run, i) => (
              <tr key={i}>
                <td title={formatDateTime(run.started_at)}>{relativeTime(run.started_at)}</td>
                <td className="bidi">{run.company_name}</td>
                <td>{SOURCE_LABELS[run.source_type]}</td>
                <td>
                  <Pill tone={run.status === "success" ? "good" : run.status === "failed" ? "bad" : "neutral"}>
                    {RUN_STATUS_LABELS[run.status]}
                    {run.error_type ? ` · ${run.error_type}` : ""}
                  </Pill>
                </td>
                <td className="num">{run.jobs_seen}</td>
                <td className="num">{run.jobs_created}</td>
                <td className="num">{run.jobs_updated}</td>
                <td className="num">{run.jobs_closed}</td>
                <td className="num">{run.jobs_failed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </main>
  );
}
