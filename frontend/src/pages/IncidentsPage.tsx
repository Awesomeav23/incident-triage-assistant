import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { ErrorBox, SeverityBadge, Spinner } from "../components/ui";
import { SEVERITIES, type IncidentDetail, type IncidentIn, type IncidentPage, type Severity } from "../types";

const PAGE = 20;

export function IncidentsPage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [severity, setSeverity] = useState<Severity | "">("");
  const [hideSynthetic, setHideSynthetic] = useState(true);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<IncidentPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => setOffset(0), [debouncedQ, severity, hideSynthetic]);

  const load = useCallback(() => {
    api
      .incidents({ q: debouncedQ, severity, limit: PAGE, offset, includeSynthetic: !hideSynthetic })
      .then((p) => {
        setPage(p);
        setError(null);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load incidents."));
  }, [debouncedQ, severity, offset, hideSynthetic]);

  useEffect(load, [load]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Past incidents</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">The resolved incidents that triage searches. Add one when you fix something new.</p>
        </div>
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500"
        >
          Add incident
        </button>
      </div>

      {notice && (
        <p className="rounded-lg bg-emerald-50 px-4 py-2 text-sm text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" role="status">
          {notice}
        </p>
      )}

      <div className="flex flex-wrap gap-2">
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search title, service, error type…"
          aria-label="Search incidents"
          className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        />
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value as Severity | "")}
          aria-label="Filter by severity"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900"
        >
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-2 px-1 text-sm text-slate-600 dark:text-slate-300">
          <input type="checkbox" checked={hideSynthetic} onChange={(e) => setHideSynthetic(e.target.checked)} className="h-4 w-4 accent-indigo-600" />
          Hide synthetic copies
        </label>
      </div>

      {error && <ErrorBox message={error} />}

      {page && (
        <>
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
                <tr>
                  <th className="px-3 py-2 font-medium">Incident</th>
                  <th className="hidden px-3 py-2 font-medium sm:table-cell">Service</th>
                  <th className="px-3 py-2 font-medium">Severity</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((inc) => (
                  <tr
                    key={inc.id}
                    onClick={() => setSelected(inc.id)}
                    className="cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50"
                  >
                    <td className="px-3 py-2.5">
                      <div className="font-medium">{inc.title}</div>
                      <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-slate-500 dark:text-slate-400">
                        <span className="font-mono">{inc.error_type}</span>
                        {inc.synthetic && <span className="rounded bg-slate-100 px-1.5 dark:bg-slate-800">synthetic</span>}
                      </div>
                    </td>
                    <td className="hidden px-3 py-2.5 text-slate-600 dark:text-slate-300 sm:table-cell">{inc.service}</td>
                    <td className="px-3 py-2.5">
                      <SeverityBadge severity={inc.severity} />
                    </td>
                  </tr>
                ))}
                {page.items.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-3 py-8 text-center text-slate-500">
                      No incidents match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-sm text-slate-600 dark:text-slate-300">
            <span>
              {page.total === 0 ? "0" : `${offset + 1}–${Math.min(offset + PAGE, page.total)}`} of {page.total}
            </span>
            <div className="flex gap-2">
              <PagerButton disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                Previous
              </PagerButton>
              <PagerButton disabled={offset + PAGE >= page.total} onClick={() => setOffset(offset + PAGE)}>
                Next
              </PagerButton>
            </div>
          </div>
        </>
      )}

      {selected !== null && <IncidentDrawer id={selected} onClose={() => setSelected(null)} />}
      {adding && (
        <AddIncidentDialog
          onClose={() => setAdding(false)}
          onAdded={(title) => {
            setAdding(false);
            setNotice(`Added “${title}”. Similar logs will now match it.`);
            setOffset(0);
            load();
          }}
        />
      )}
    </div>
  );
}

function PagerButton({ disabled, onClick, children }: { disabled: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-lg border border-slate-300 px-3 py-1.5 disabled:opacity-40 dark:border-slate-700"
    >
      {children}
    </button>
  );
}

function Overlay({ onClose, children, side }: { onClose: () => void; children: React.ReactNode; side?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-20 flex bg-slate-900/40" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
        className={
          side
            ? "ml-auto h-full w-full max-w-xl overflow-y-auto bg-white p-5 shadow-xl dark:bg-slate-900"
            : "m-auto max-h-[92vh] w-full max-w-xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl dark:bg-slate-900"
        }
      >
        {children}
      </div>
    </div>
  );
}

function IncidentDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const [inc, setInc] = useState<IncidentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.incident(id).then(setInc).catch((e) => setError(e instanceof ApiError ? e.message : "Could not load."));
  }, [id]);
  return (
    <Overlay onClose={onClose} side>
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">{inc?.title ?? "Incident"}</h2>
        <button type="button" onClick={onClose} aria-label="Close" className="text-xl leading-none text-slate-500 hover:text-slate-800">
          ×
        </button>
      </div>
      {error && <ErrorBox message={error} />}
      {!inc && !error && <Spinner />}
      {inc && (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2 text-slate-600 dark:text-slate-300">
            <SeverityBadge severity={inc.severity} />
            <span>{inc.service}</span>·<span className="font-mono text-xs">{inc.error_type}</span>
          </div>
          <Field label="Root cause">{inc.root_cause}</Field>
          <Field label="Fix">{inc.fix}</Field>
          <Field label="Log">
            <pre className="max-h-80 overflow-auto rounded-lg bg-slate-100 p-3 font-mono text-xs leading-relaxed dark:bg-slate-950">{inc.log_text}</pre>
          </Field>
          {inc.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {inc.tags.map((t) => (
                <span key={t} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs dark:bg-slate-800">
                  {t}
                </span>
              ))}
            </div>
          )}
          <p className="text-xs text-slate-500">
            #{inc.id}
            {inc.family ? ` · family ${inc.family}` : ""}
            {inc.synthetic ? " · synthetic recurrence" : ""}
          </p>
        </div>
      )}
    </Overlay>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</h3>
      <div className="leading-relaxed">{children}</div>
    </div>
  );
}

const EMPTY: IncidentIn = { title: "", service: "", error_type: "", log_text: "", root_cause: "", fix: "", severity: "medium", tags: [] };

export function AddIncidentDialog({ onClose, onAdded }: { onClose: () => void; onAdded: (title: string) => void }) {
  const [form, setForm] = useState<IncidentIn>(EMPTY);
  const [tags, setTags] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof IncidentIn) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const problems = [
    form.title.trim().length < 3 && "title (3+ characters)",
    !form.service.trim() && "service",
    !form.error_type.trim() && "error type",
    form.log_text.trim().length < 10 && "log (10+ characters)",
    !form.root_cause.trim() && "root cause",
    !form.fix.trim() && "fix",
  ].filter(Boolean) as string[];

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (problems.length) {
      setError(`Please fill in: ${problems.join(", ")}.`);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
      const res = await api.addIncident({ ...form, tags: tagList });
      onAdded(res.title);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save.");
      setSaving(false);
    }
  }

  const input = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";
  return (
    <Overlay onClose={onClose}>
      <form onSubmit={save} className="space-y-3" noValidate>
        <h2 className="text-lg font-semibold">Add a resolved incident</h2>
        <Labeled label="Title">
          <input className={input} value={form.title} onChange={set("title")} maxLength={200} />
        </Labeled>
        <div className="grid gap-3 sm:grid-cols-2">
          <Labeled label="Service">
            <input className={input} value={form.service} onChange={set("service")} maxLength={100} />
          </Labeled>
          <Labeled label="Severity">
            <select className={input} value={form.severity} onChange={set("severity")}>
              {SEVERITIES.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Labeled>
        </div>
        <Labeled label="Error type">
          <input className={`${input} font-mono`} value={form.error_type} onChange={set("error_type")} placeholder="e.g. redis.exceptions.ConnectionError" />
        </Labeled>
        <Labeled label="Log / stack trace">
          <textarea className={`${input} h-28 font-mono text-xs`} value={form.log_text} onChange={set("log_text")} spellCheck={false} />
        </Labeled>
        <Labeled label="Root cause">
          <textarea className={`${input} h-16`} value={form.root_cause} onChange={set("root_cause")} />
        </Labeled>
        <Labeled label="Fix">
          <textarea className={`${input} h-16`} value={form.fix} onChange={set("fix")} />
        </Labeled>
        <Labeled label="Tags (comma-separated, optional)">
          <input className={input} value={tags} onChange={(e) => setTags(e.target.value)} />
        </Labeled>
        {error && <ErrorBox message={error} />}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="rounded-lg border border-slate-300 px-4 py-2 text-sm dark:border-slate-700">
            Cancel
          </button>
          <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-50">
            {saving && <Spinner />}
            Save incident
          </button>
        </div>
      </form>
    </Overlay>
  );
}

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm font-medium">
      {label}
      {children}
    </label>
  );
}
