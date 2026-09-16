import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { JobPage } from "./pages/JobPage";
import { MatchesPage } from "./pages/MatchesPage";
import { ProfilePage } from "./pages/ProfilePage";
import { StatusPage } from "./pages/StatusPage";

const LINKS = [
  { to: "/", label: "התאמות" },
  { to: "/status", label: "מצב המערכת" },
  { to: "/profile", label: "הפרופיל שלי" },
];

export function App() {
  return (
    <BrowserRouter basename="/app">
      <a className="skip-link" href="#main">
        דלג לתוכן
      </a>
      <nav className="nav" aria-label="ניווט ראשי">
        <div className="nav__inner">
          <NavLink to="/" className="nav__brand">
            <span className="nav__logo" aria-hidden="true">
              ✓
            </span>
            Job Intelligence
          </NavLink>
          <div className="nav__links">
            {LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === "/"}
                className={({ isActive }) => `nav__link${isActive ? " nav__link--active" : ""}`}
              >
                {link.label}
              </NavLink>
            ))}
          </div>
        </div>
      </nav>
      <Routes>
        <Route path="/" element={<MatchesPage />} />
        <Route path="/jobs/:id" element={<JobPage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="*" element={<MatchesPage />} />
      </Routes>
    </BrowserRouter>
  );
}
