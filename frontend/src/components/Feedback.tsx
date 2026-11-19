import { useState } from "react";
import { api, ApiError } from "../api";
import { SEVERITIES, type FeedbackOut, type Severity, type TriageResponse } from "../types";
import { Card, ErrorBox, Spinner } from "./ui";

const INPUT =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";

/** 👍 / ✏️ under a triage result. One feedback per triage run (the API returns 409 after). */
export function FeedbackBar({ result }: { result: TriageResponse }) {
  const [mode, setMode] = useState<"idle" | "correcting">("idle");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<FeedbackOut | null>(null);

  if (result.run_id === null) return null;
  const runId = result.run_id;

  async function send(body: Parameters<typeof api.feedback>[0]) {
    setSending(true);
    setError(null);
    try {
      setDone(await api.feedback(body));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't save feedback.");
    } finally {
      setSending(false);
    }
  }

  if (done) {
    return (
      <Card>
        <p className="text-sm" role="status" data-testid="feedback-done">
          {done.correct ? "👍 Marked as correct." : "✏️ Correction saved."}
          {done.indexed_as !== null && <> Added to the incident library as #{done.indexed_as}, so similar logs will match it next time.</>}
          {!done.correct && done.indexed_as === null && <> This answer won't be reused for repeats of this log.</>}
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Was this right?</span>
        <button
          type="button"
          disabled={sending}
          onClick={() => send({ triage_run_id: runId, correct: true })}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:border-emerald-400 hover:bg-emerald-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          👍 Correct
        </button>
        <button
          type="button"
          disabled={sending}
          onClick={() => setMode(mode === "correcting" ? "idle" : "correcting")}
          aria-expanded={mode === "correcting"}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm hover:border-indigo-400 hover:bg-indigo-50 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          ✏️ Correct it
        </button>
        {sending && <Spinner />}
      </div>
      {error && (
        <div className="mt-3">
          <ErrorBox message={error} />
        </div>
      )}
      {mode === "correcting" && <CorrectionForm result={result} sending={sending} onSubmit={send} />}
    </Card>
  );
}

function CorrectionForm({
  result,
  sending,
  onSubmit,
}: {
  result: TriageResponse;
  sending: boolean;
  onSubmit: (body: Parameters<typeof api.feedback>[0]) => void;
}) {
  const [rootCause, setRootCause] = useState("");
  const [fix, setFix] = useState("");
  const [severity, setSeverity] = useState<Severity>(result.severity);
  const [index, setIndex] = useState(true);
  const [title, setTitle] = useState("");
  const [service, setService] = useState("");
  const missing = index && (!rootCause.trim() || !fix.trim());

  return (
    <form
      className="mt-4 space-y-3"
      aria-label="Correction"
      onSubmit={(e) => {
        e.preventDefault();
        if (missing) return;
        onSubmit({
          triage_run_id: result.run_id!,
          correct: false,
          root_cause: rootCause.trim() || undefined,
          fix: fix.trim() || undefined,
          severity,
          index,
          title: index && title.trim().length >= 3 ? title.trim() : undefined,
          service: index && service.trim() ? service.trim() : undefined,
        });
      }}
    >
      <label className="block text-sm">
        <span className="mb-1 block font-medium">Actual root cause</span>
        <textarea value={rootCause} onChange={(e) => setRootCause(e.target.value)} rows={3} className={INPUT} />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">Actual fix</span>
        <textarea value={fix} onChange={(e) => setFix(e.target.value)} rows={3} className={INPUT} />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">Severity</span>
        <select value={severity} onChange={(e) => setSeverity(e.target.value as Severity)} className={INPUT}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={index} onChange={(e) => setIndex(e.target.checked)} className="h-4 w-4 accent-indigo-600" />
        Add to the incident library so future logs like this match it
      </label>
      {index && (
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Title (optional)</span>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="from the log's first line" className={INPUT} />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Service (optional)</span>
            <input value={service} onChange={(e) => setService(e.target.value)} placeholder="unknown" className={INPUT} />
          </label>
        </div>
      )}
      {missing && <p className="text-xs text-slate-500 dark:text-slate-400">Root cause and fix are needed to add it to the library.</p>}
      <button
        type="submit"
        disabled={sending || missing}
        className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Save correction
      </button>
    </form>
  );
}
