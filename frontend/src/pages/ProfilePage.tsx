import { useState, type FormEvent } from "react";
import {
  activateProfile,
  createTargetRole,
  fetchProfiles,
  fetchTargetRoles,
  updateTargetRole,
  uploadResume,
} from "../api/client";
import type { TargetRole } from "../api/types";
import { EmptyState, Pill, Skeletons, Toggle } from "../components/ui";
import { useAsync } from "../hooks/useAsync";
import { extractedSkills, formatDateTime, relativeTime } from "../lib/format";

const splitList = (value: string) =>
  value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);

export function ProfilePage() {
  const profiles = useAsync(fetchProfiles, []);
  const roles = useAsync(fetchTargetRoles, []);

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
  profiles: { id: number; version: number; filename: string; is_active: boolean; activated_at: string | null; created_at: string; structured_profile: Record<string, unknown> }[];
  loading: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const active = profiles.find((p) => p.is_active);

  async function upload(file: File) {
    setBusy(true);
    setMessage(null);
    try {
      await uploadResume(file);
      setMessage({ ok: true, text: "קורות החיים נקלטו, וכל המשרות חושבו מחדש." });
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
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong className="bidi">{active.filename}</strong>
              <div className="faint">
                גרסה {active.version} · הופעלה {relativeTime(active.activated_at)}
              </div>
            </div>
            <Pill tone="good">פעיל</Pill>
          </div>
          {extractedSkills(active.structured_profile).length > 0 && (
            <div className="match__tags">
              {extractedSkills(active.structured_profile)
                .slice(0, 30)
                .map((skill) => (
                  <Pill key={skill}>{skill}</Pill>
                ))}
            </div>
          )}
        </div>
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
          {busy ? "מעלה ומחשב מחדש…" : "גרור לכאן קורות חיים חדשים, או לחץ לבחירה"}
        </div>
        <div className="faint">PDF או DOCX, עד 10MB. גרסאות קודמות נשמרות.</div>
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
