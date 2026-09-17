import { useEffect, useState, type FormEvent } from "react";
import {
  activateProfile,
  createTargetRole,
  fetchProfileText,
  fetchProfiles,
  fetchTargetRoles,
  updateTargetRole,
  uploadResume,
} from "../api/client";
import type { CandidateProfile, CandidateProfileText, TargetRole } from "../api/types";
import { EmptyState, Pill, Skeletons, Toggle } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { PROFILE_SECTIONS, formatDateTime, relativeTime, stringList } from "../lib/format";

const splitList = (value: string) =>
  value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

export function ProfilePage() {
  const profiles = useAsync(fetchProfiles, []);
  const roles = useAsync(fetchTargetRoles, []);

  // While the active profile is still being processed in the background
  // (LLM extraction + rescoring every job), keep asking until it's done.
  const pending = (profiles.data ?? []).some((p) => p.is_active && p.processing_status === "pending");
  useEffect(() => {
    if (!pending) return;
    const handle = window.setInterval(profiles.reload, 3000);
    return () => window.clearInterval(handle);
  }, [pending, profiles.reload]);

  return (
    <main id="main" className="page">
      <header className="page__header">
        <div>
          <h1 className="page__title">הפרופיל שלי</h1>
          <p className="page__subtitle">
            קורות החיים ותפקידי היעד שמולם כל משרה נמדדת. כל שינוי כאן מחשב מחדש את כל ההתאמות.
          </p>
        </div>
      </header>

      <h2 className="section-title" style={{ marginTop: 8 }}>
        קורות חיים
      </h2>
      <ResumeSection
        profiles={profiles.data ?? []}
        loading={profiles.loading && !profiles.data}
        onChanged={profiles.reload}
      />

      <h2 className="section-title">תפקידי יעד</h2>
      <RolesSection roles={roles.data ?? []} loading={roles.loading && !roles.data} onChanged={roles.reload} />
    </main>
  );
}

function ResumeSection({
  profiles,
  loading,
  onChanged,
}: {
  profiles: CandidateProfile[];
  loading: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const active = profiles.find((p) => p.is_active);
  const processing = active?.processing_status === "pending";

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      const profile = await uploadResume(file);
      const known = profiles.some((p) => p.id === profile.id);
      setMessage({
        ok: true,
        text: known
          ? "הקובץ הזה כבר במערכת - הופעל מחדש. ההתאמות מתעדכנות ברקע."
          : "הקובץ נקרא ונשמר. עכשיו מחלץ פרופיל ומחשב מחדש את כל המשרות - זה רץ ברקע.",
      });
      onChanged();
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : "ההעלאה נכשלה" });
    } finally {
      setBusy(false);
    }
  }

  async function activate(id: number) {
    setBusy(true);
    try {
      await activateProfile(id);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeletons count={1} />;

  return (
    <div className="stack">
      {active ? (
        <>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <strong className="bidi">{active.filename}</strong>
                <div className="faint">
                  גרסה {active.version} · הופעלה {relativeTime(active.activated_at)}
                </div>
              </div>
              {processing ? (
                <Pill tone="accent">מעבד ברקע…</Pill>
              ) : active.processing_status === "failed" ? (
                <Pill tone="bad">העיבוד נכשל</Pill>
              ) : (
                <Pill tone="good">פעיל</Pill>
              )}
            </div>
            {processing && (
              <div className="alert alert--ok" role="status" style={{ marginTop: 12 }}>
                <span className="pulse" aria-hidden="true" /> הקובץ נקרא ונשמר. עכשיו רץ חילוץ
                הפרופיל (מודל שפה מקומי) ואחריו דירוג מחדש של כל המשרות - בדרך כלל 1-3 דקות.
                העמוד יתעדכן לבד.
              </div>
            )}
            {active.processing_status === "failed" && (
              <div className="alert" role="alert" style={{ marginTop: 12 }}>
                {active.processing_note ?? "העיבוד נכשל"} - ההתאמות עדיין עובדות על הטקסט
                המלא.
              </div>
            )}
            {active.processing_status === "done" && active.processing_note && (
              <div className="faint" style={{ marginTop: 8 }}>
                {active.processing_note}
              </div>
            )}
          </div>
          {!processing && (
            <ScanExplainer profileId={active.id} structured={active.structured_profile} />
          )}
        </>
      ) : (
        <EmptyState title="עוד אין קורות חיים">העלה PDF או DOCX כדי להתחיל לקבל התאמות.</EmptyState>
      )}

      <label
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) void upload(file);
        }}
      >
        <input
          type="file"
          accept=".pdf,.docx"
          style={{ display: "none" }}
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
        <div style={{ fontWeight: 600, color: "var(--text)" }}>
          {busy ? "קורא את הקובץ…" : "גרור לכאן קורות חיים חדשים, או לחץ לבחירה"}
        </div>
        <div className="faint">
          PDF או DOCX, עד 10MB. גרסאות קודמות נשמרות. הקריאה לוקחת שניות; החילוץ והדירוג
          ממשיכים ברקע.
        </div>
      </label>

      {message && (
        <div className={`alert${message.ok ? " alert--ok" : ""}`} role="status">
          {message.text}
        </div>
      )}

      {profiles.length > 1 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>גרסה</th>
                <th>קובץ</th>
                <th>הועלה</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.id}>
                  <td className="num">{p.version}</td>
                  <td className="bidi">{p.filename}</td>
                  <td title={formatDateTime(p.created_at)}>{relativeTime(p.created_at)}</td>
                  <td>
                    {p.is_active ? (
                      <Pill tone="good">פעיל</Pill>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--small"
                        disabled={busy}
                        onClick={() => activate(p.id)}
                      >
                        הפעל גרסה זו
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ScanExplainer({
  profileId,
  structured,
}: {
  profileId: number;
  structured: Record<string, unknown>;
}) {
  const [text, setText] = useState<CandidateProfileText | null>(null);
  const [loadingText, setLoadingText] = useState(false);
  const sections = PROFILE_SECTIONS.map((s) => ({ ...s, values: stringList(structured, s.key) }))
    .filter((s) => s.values.length > 0);
  const years = structured["years_of_experience"];
  const seniority = structured["seniority"];

  async function showText() {
    if (text) return;
    setLoadingText(true);
    try {
      setText(await fetchProfileText(profileId));
    } finally {
      setLoadingText(false);
    }
  }

  return (
    <>
      <div className="card">
        <h3 style={{ margin: "0 0 12px", fontSize: 17 }}>מה הבוט עושה עם קורות החיים שלך</h3>
        <ol className="steps" style={{ margin: 0, padding: 0, listStyle: "none" }}>
          <li className="step">
            <div>
              <strong>קורא את הטקסט מהקובץ</strong>
              PDF או DOCX. רק טקסט - תמונות, טבלאות מורכבות ועיצוב לא נקראים. הטקסט המלא
              שנקרא מוצג למטה.
            </div>
          </li>
          <li className="step">
            <div>
              <strong>מחלץ פרופיל מובנה</strong>
              מודל שפה מקומי (חינמי, רץ על המחשב) מעתיק מהטקסט שפות תכנות, פריימוורקים, בסיסי
              נתונים, השכלה ופרויקטים. הוותק לא נקבע על ידי המודל אלא לפי כלל: קורות חיים בלי
              פרק "ניסיון תעסוקתי" = ג'וניור, 0 שנים; עם פרק כזה המודל מעריך את השנים. זה שכבת
              עזר - אם משהו חסר כאן, ההתאמה עדיין משתמשת בטקסט המלא.
            </div>
          </li>
          <li className="step">
            <div>
              <strong>מחשב "טביעת אצבע" סמנטית</strong>
              הטקסט המלא הופך לווקטור (embedding) שמשווים לכל משרה - לפי משמעות, לא לפי מילים
              זהות. קורות חיים ארוכים נקראים בחלקים כדי שדף שני ושלישי ייספרו.
            </div>
          </li>
          <li className="step">
            <div>
              <strong>מדרג כל משרה</strong>
              רמת הוותק שהמשרה דורשת, חפיפת כישורים, דמיון סמנטי, מיקום וטריות - עם התאמת
              התפקיד כשער. הפירוט המלא מופיע בעמוד של כל משרה.
            </div>
          </li>
        </ol>
      </div>

      <div className="card">
        <h3 style={{ margin: "0 0 4px", fontSize: 17 }}>מה הסריקה הבינה</h3>
        <p className="faint" style={{ margin: "0 0 12px" }}>
          תעבור על זה בעין: אם כישור חשוב חסר, כדאי שיופיע במפורש בקורות החיים.
        </p>
        {(typeof years === "number" || typeof seniority === "string") && (
          <div className="row" style={{ marginBottom: 12 }}>
            {typeof years === "number" && <Pill tone="accent">{years} שנות ניסיון</Pill>}
            {typeof seniority === "string" && <Pill tone="accent">רמה: {seniority}</Pill>}
          </div>
        )}
        {sections.length === 0 ? (
          <div className="alert" role="status">
            החילוץ המובנה חזר ריק - המודל המקומי לא הצליח (או לא רץ). ההתאמות עדיין עובדות על
            הטקסט המלא; אפשר להריץ שוב עם <code>python -m app.cli rebuild-profile</code>.
          </div>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            {sections.map((section) => (
              <div key={section.key}>
                <div className="faint" style={{ marginBottom: 4 }}>
                  {section.label}
                </div>
                <div className="match__tags" style={{ marginTop: 0 }}>
                  {section.values.slice(0, 40).map((value) => (
                    <Pill key={value}>{value}</Pill>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
        <details className="raw" style={{ marginTop: 16 }} onToggle={(e) => e.currentTarget.open && void showText()}>
          <summary>הטקסט המלא שנקרא מהקובץ</summary>
          {loadingText && <div className="faint">טוען…</div>}
          {text && (
            <div className="raw__text bidi" dir="auto">
              {text.raw_text}
            </div>
          )}
        </details>
      </div>
    </>
  );
}

function RolesSection({
  roles,
  loading,
  onChanged,
}: {
  roles: TargetRole[];
  loading: boolean;
  onChanged: () => void;
}) {
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle(role: TargetRole, enabled: boolean) {
    setBusy(true);
    try {
      await updateTargetRole(role.id, { enabled });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const years = String(form.get("max_expected_years") ?? "").trim();
    setBusy(true);
    setError(null);
    try {
      await createTargetRole({
        canonical_name: String(form.get("canonical_name") ?? "").trim(),
        aliases: splitList(String(form.get("aliases") ?? "")),
        positive_keywords: splitList(String(form.get("positive_keywords") ?? "")),
        negative_keywords: splitList(String(form.get("negative_keywords") ?? "")),
        preferred_skills: splitList(String(form.get("preferred_skills") ?? "")),
        max_expected_years: years ? Number(years) : null,
        enabled: true,
      });
      setShowForm(false);
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "לא נשמר");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <Skeletons count={1} />;

  return (
    <div className="stack">
      {roles.map((role) => (
        <div className="card" key={role.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong className="bidi" style={{ fontSize: 17 }}>
              {role.canonical_name}
            </strong>
            <Toggle label={role.enabled ? "פעיל" : "כבוי"} checked={role.enabled} onChange={(v) => toggle(role, v)} />
          </div>
          <div className="match__tags">
            {role.aliases.map((a) => (
              <Pill key={`a-${a}`} tone="accent" title="שם נוסף לתפקיד">
                {a}
              </Pill>
            ))}
            {role.positive_keywords.map((k) => (
              <Pill key={`p-${k}`} tone="good" title="מילת מפתח חיובית">
                {k}
              </Pill>
            ))}
            {role.negative_keywords.map((k) => (
              <Pill key={`n-${k}`} tone="bad" title="מילה שפוסלת">
                {k}
              </Pill>
            ))}
            {role.max_expected_years !== null && (
              <Pill title="שנות ניסיון מקסימליות שמתאימות לך">עד {role.max_expected_years} שנות ניסיון</Pill>
            )}
          </div>
        </div>
      ))}

      <p className="faint" style={{ margin: 0 }}>
        כל שינוי כאן מחשב מחדש את ההתאמות ברקע (כדקה-שתיים) - הרשימה ב"התאמות" תתעדכן
        לבד.
      </p>
      {showForm ? (
        <form className="card stack" onSubmit={submit}>
          <div className="form-grid">
            <label className="field">
              שם התפקיד
              <input name="canonical_name" required placeholder="Junior Software Engineer" />
            </label>
            <label className="field">
              שמות נוספים (מופרדים בפסיק)
              <input name="aliases" placeholder="Software Engineer I, Entry Level Developer" />
            </label>
            <label className="field">
              מילות מפתח חיוביות
              <input name="positive_keywords" placeholder="python, backend, full stack" />
            </label>
            <label className="field">
              מילים שפוסלות משרה
              <input name="negative_keywords" placeholder="senior, lead, manager" />
            </label>
            <label className="field">
              כישורים מועדפים
              <input name="preferred_skills" placeholder="python, docker, sql" />
            </label>
            <label className="field">
              עד כמה שנות ניסיון
              <input name="max_expected_years" type="number" min={0} max={30} placeholder="2" />
            </label>
          </div>
          {error && (
            <div className="alert" role="alert">
              {error}
            </div>
          )}
          <div className="row">
            <button type="submit" className="btn btn--primary" disabled={busy}>
              שמור תפקיד
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => setShowForm(false)}>
              ביטול
            </button>
          </div>
        </form>
      ) : (
        <div>
          <button type="button" className="btn" onClick={() => setShowForm(true)}>
            + תפקיד יעד חדש
          </button>
        </div>
      )}
    </div>
  );
}
