import { Fragment, type ReactNode } from "react";
import type { Severity } from "../types";

const SEVERITY_STYLE: Record<Severity, string> = {
  critical: "bg-red-600 text-white",
  high: "bg-orange-500 text-white",
  medium: "bg-amber-300 text-amber-950",
  low: "bg-sky-200 text-sky-950 dark:bg-sky-800 dark:text-sky-50",
  info: "bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100",
};

export function SeverityBadge({ severity, size = "sm" }: { severity: Severity; size?: "sm" | "lg" }) {
  const pad = size === "lg" ? "px-3 py-1 text-sm" : "px-2 py-0.5 text-xs";
  return (
    <span
      className={`inline-block rounded-full font-semibold uppercase tracking-wide ${pad} ${SEVERITY_STYLE[severity]}`}
      data-testid="severity-badge"
    >
      {severity}
    </span>
  );
}

/** Horizontal bar for a 0..1 value, with a label. */
export function Meter({ value, label, tone = "indigo" }: { value: number; label: string; tone?: "indigo" | "emerald" }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const fill = tone === "emerald" ? "bg-emerald-500" : "bg-indigo-500";
  return (
    <div className="flex items-center gap-2" title={`${label}: ${pct}%`}>
      <div
        className="h-2 w-24 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700"
        role="meter"
        aria-label={label}
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className={`h-full ${fill}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-600 dark:text-slate-300">{pct}%</span>
    </div>
  );
}

const SEGMENTS = [
  { key: "embed", label: "Embed", color: "bg-sky-400" },
  { key: "search", label: "Search", color: "bg-emerald-400" },
  { key: "llm", label: "Claude", color: "bg-indigo-500" },
] as const;

export function LatencyBar({
  latency,
  answerLabel = "Claude",
}: {
  latency: { embed: number; search: number; llm: number; total: number };
  answerLabel?: string;
}) {
  const total = Math.max(latency.total, 1);
  return (
    <div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">
        {SEGMENTS.map((s) => (
          <div
            key={s.key}
            className={s.color}
            style={{ width: `${(latency[s.key] / total) * 100}%`, minWidth: latency[s.key] > 0 ? 2 : 0 }}
          />
        ))}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
        {SEGMENTS.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5">
            <span className={`inline-block h-2 w-2 rounded-full ${s.color}`} />
            {s.key === "llm" ? answerLabel : s.label} {formatMs(latency[s.key])}
          </span>
        ))}
        <span className="font-medium text-slate-800 dark:text-slate-100">Total {formatMs(latency.total)}</span>
      </div>
    </div>
  );
}

export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** Render `backtick` spans as inline code; everything else as plain text. */
export function RichText({ text }: { text: string }) {
  const parts = text.split(/(`[^`]+`)/g);
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith("`") && p.endsWith("`") && p.length > 2 ? (
          <code key={i} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] dark:bg-slate-800">
            {p.slice(1, -1)}
          </code>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900 sm:p-5 ${className}`}>
      {children}
    </div>
  );
}

export function Spinner() {
  return (
    <span
      className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent"
      aria-hidden
    />
  );
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
      {message}
    </div>
  );
}
