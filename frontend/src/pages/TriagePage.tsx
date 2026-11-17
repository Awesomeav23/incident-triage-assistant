import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { ResultCard } from "../components/ResultCard";
import { ErrorBox, Spinner } from "../components/ui";
import type { Sample, TriageResponse } from "../types";

const MAX_CHARS = 50_000;
const MIN_CHARS = 10;

export function TriagePage() {
  const [log, setLog] = useState("");
  const [topK, setTopK] = useState(3);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TriageResponse | null>(null);
  const timer = useRef<ReturnType<typeof setInterval>>();

  useEffect(() => {
    api.samples().then(setSamples).catch(() => setSamples([]));
    return () => clearInterval(timer.current);
  }, []);

  const tooShort = log.trim().length < MIN_CHARS;
  const tooLong = log.length > MAX_CHARS;

  async function submit() {
    if (tooShort || tooLong || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    const start = performance.now();
    setElapsed(0);
    timer.current = setInterval(() => setElapsed((performance.now() - start) / 1000), 100);
    try {
      setResult(await api.triage({ log, top_k: topK }));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    } finally {
      clearInterval(timer.current);
      setLoading(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div className="space-y-3">
        <div>
          <h1 className="text-xl font-semibold">Triage an error</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Paste a stack trace or failed CI log. It's matched against past resolved incidents to find the likely root cause and fix.
          </p>
        </div>

        {samples.length > 0 && (
          <div className="flex flex-wrap gap-2" aria-label="Sample logs">
            <span className="self-center text-xs text-slate-500 dark:text-slate-400">Try a sample:</span>
            {samples.map((s) => (
              <button
                key={s.name}
                type="button"
                onClick={() => {
                  setLog(s.log);
                  setResult(null);
                  setError(null);
                }}
                className="rounded-full border border-slate-300 px-3 py-1 text-xs hover:border-indigo-400 hover:bg-indigo-50 dark:border-slate-700 dark:hover:bg-slate-800"
              >
                {s.name}
              </button>
            ))}
          </div>
        )}

        <label className="block">
          <span className="sr-only">Error log</span>
          <textarea
            value={log}
            onChange={(e) => setLog(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
            }}
            placeholder={"Traceback (most recent call last):\n  ..."}
            spellCheck={false}
            className="h-72 w-full resize-y rounded-xl border border-slate-300 bg-white p-3 font-mono text-xs leading-relaxed shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-200 dark:border-slate-700 dark:bg-slate-900 dark:focus:ring-indigo-900 lg:h-96"
          />
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={tooShort || tooLong || loading}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading && <Spinner />}
            {loading ? `Triaging… ${elapsed.toFixed(1)} s` : "Triage"}
          </button>
          <label className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
            Matches
            <select
              value={topK}
              onChange={(e) => setTopK(Number(e.target.value))}
              className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
            >
              {[1, 2, 3, 5, 10].map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </label>
          {log && (
            <button type="button" onClick={() => { setLog(""); setResult(null); setError(null); }} className="text-sm text-slate-500 hover:underline">
              Clear
            </button>
          )}
          <span className={`ml-auto text-xs tabular-nums ${tooLong ? "text-red-600" : "text-slate-500 dark:text-slate-400"}`}>
            {log.length.toLocaleString()} / {MAX_CHARS.toLocaleString()}
          </span>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">⌘/Ctrl + Enter to submit · secrets in the log are redacted before analysis</p>
      </div>

      <div className="min-w-0">
        {error && <ErrorBox message={error} />}
        {loading && !result && (
          <div className="flex h-full min-h-48 items-center justify-center rounded-xl border border-dashed border-slate-300 text-sm text-slate-500 dark:border-slate-700">
            Searching past incidents and asking Claude…
          </div>
        )}
        {result && <ResultCard result={result} />}
        {!loading && !result && !error && (
          <div className="flex h-full min-h-48 items-center justify-center rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500 dark:border-slate-700">
            The diagnosis will appear here.
          </div>
        )}
      </div>
    </div>
  );
}
