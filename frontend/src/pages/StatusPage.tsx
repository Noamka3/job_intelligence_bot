import { useEffect } from "react";
import { fetchStats } from "../api/client";
import { Pill, Skeletons, StatTile } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { RUN_STATUS_LABELS, SOURCE_LABELS, formatDateTime, relativeTime } from "../lib/format";

const REFRESH_MS = 60_000;

export function StatusPage() {
  const stats = useAsync(fetchStats, []);

  useEffect(() => {
    const handle = window.setInterval(stats.reload, REFRESH_MS);
    return () => window.clearInterval(handle);
  }, [stats.reload]);

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
  const lastCrawlAge = s.last_crawl_at ? (Date.now() - new Date(s.last_crawl_at).getTime()) / 60000 : Infinity;
  const stale = lastCrawlAge > 20;

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">מצב המערכת</h1>
          <p className="page__subtitle">
            <span className={`pulse${stale ? " pulse--stale" : ""}`} aria-hidden="true" />{" "}
            {s.last_crawl_at
              ? `סריקה אחרונה ${relativeTime(s.last_crawl_at)}`
              : "עוד לא בוצעה סריקה"}
            {stale && " - נראה שהמתזמן לא רץ (בדוק שה-worker ו-beat פעילים)"}
          </p>
        </div>
      </header>

      <div className="tiles">
        <StatTile value={s.active_israel_jobs} label="משרות פעילות בישראל" tone="good" />
        <StatTile value={s.active_unknown_location_jobs} label="משרות ללא מיקום ידוע" />
        <StatTile value={s.active_jobs} label="משרות פעילות סה״כ" />
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
