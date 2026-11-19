import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import { Card, ErrorBox, SeverityBadge, Spinner } from "../components/ui";
import { DEMO_MODEL, SEVERITIES, type Stats } from "../types";

export function StatsPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .stats(10)
      .then(setStats)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Couldn't load stats."));
  }, []);

  if (error) return <ErrorBox message={error} />;
  if (!stats)
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Spinner /> Loading…
      </div>
    );

  const reused = stats.triage_requests ? stats.duplicates_suppressed / stats.triage_requests : 0;
  const maxSev = Math.max(1, ...SEVERITIES.map((s) => stats.severity_counts[s] ?? 0));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Stats</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">How triage is being used, how often repeated logs were answered from history, and what engineers said about the answers.</p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Triage requests" value={stats.triage_requests} />
        <Tile
          label="Answered from history"
          value={stats.duplicates_suppressed}
          note={stats.triage_requests ? `${Math.round(reused * 100)}% of requests, no model call` : undefined}
        />
        <Tile
          label="Feedback accuracy"
          value={stats.feedback_accuracy === null ? "—" : `${Math.round(stats.feedback_accuracy * 100)}%`}
          note={`${stats.feedback_correct} of ${stats.feedback_count} marked correct`}
        />
        <Tile label="Incidents indexed" value={stats.incidents_indexed} note={`${stats.indexed_from_feedback} added from feedback`} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card>
          <h2 className="mb-3 text-sm font-semibold">Severity of triaged logs</h2>
          <ul className="space-y-2" aria-label="Severity counts">
            {SEVERITIES.map((s) => {
              const n = stats.severity_counts[s] ?? 0;
              return (
                <li key={s} className="flex items-center gap-3 text-sm">
                  <span className="w-20 shrink-0">
                    <SeverityBadge severity={s} />
                  </span>
                  <span className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
                    <span className="block h-full bg-indigo-500" style={{ width: `${(n / maxSev) * 100}%` }} />
                  </span>
                  <span className="w-8 text-right tabular-nums">{n}</span>
                </li>
              );
            })}
          </ul>
        </Card>

        <Card>
          <h2 className="mb-3 text-sm font-semibold">Recent triages</h2>
          {stats.recent_runs.length === 0 ? (
            <p className="text-sm text-slate-500 dark:text-slate-400">No triages yet. Paste a log on the Triage page.</p>
          ) : (
            <ul className="divide-y divide-slate-100 dark:divide-slate-800" aria-label="Recent triages">
              {stats.recent_runs.map((r) => (
                <li key={r.id} className="flex flex-col gap-1.5 py-2.5 text-sm sm:flex-row sm:items-start sm:gap-3">
                  <span className="shrink-0">
                    <SeverityBadge severity={r.severity} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2">{r.summary}</span>
                    <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">
                      #{r.id} · {new Date(r.created_at).toLocaleString()} · {r.model === DEMO_MODEL ? "demo" : r.model}
                      {r.duplicate_count > 0 && ` · seen ${r.duplicate_count + 1}×`}
                    </span>
                  </span>
                  {r.feedback && (
                    <span className="shrink-0 text-xs" data-testid="run-feedback">
                      {r.feedback === "correct" ? "👍 correct" : "✏️ corrected"}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: number | string; note?: string }) {
  return (
    <Card>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {note && <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{note}</p>}
    </Card>
  );
}
