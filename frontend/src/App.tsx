import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { IncidentsPage } from "./pages/IncidentsPage";
import { TriagePage } from "./pages/TriagePage";

export function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <span className="font-semibold">🩺 Incident Triage</span>
          <nav className="flex gap-1 text-sm">
            <Tab to="/">Triage</Tab>
            <Tab to="/incidents">Incidents</Tab>
          </nav>
          <HealthIndicator />
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<TriagePage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
        </Routes>
      </main>
    </div>
  );
}

function Tab({ to, children }: { to: string; children: string }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        `rounded-md px-3 py-1.5 ${isActive ? "bg-slate-100 font-medium dark:bg-slate-800" : "text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100"}`
      }
    >
      {children}
    </NavLink>
  );
}

function HealthIndicator() {
  const [state, setState] = useState<{ ok: boolean; count?: number; mode?: string } | null>(null);
  useEffect(() => {
    const check = () =>
      api
        .health()
        .then((h) => setState({ ok: true, count: h.incidents_indexed, mode: h.llm_mode }))
        .catch(() => setState({ ok: false }));
    check();
    const t = setInterval(check, 30_000);
    return () => clearInterval(t);
  }, []);
  if (!state) return null;
  return (
    <span className="ml-auto flex items-center gap-3 text-xs text-slate-600 dark:text-slate-400" role="status">
      {state.mode === "demo" && (
        <span className="rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-900 dark:bg-amber-900 dark:text-amber-100" title="No AI calls, no cost">
          Demo mode
        </span>
      )}
      <span className="flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${state.ok ? "bg-emerald-500" : "bg-red-500"}`} />
        {state.ok ? `${state.count} incidents indexed` : "API offline"}
      </span>
    </span>
  );
}
