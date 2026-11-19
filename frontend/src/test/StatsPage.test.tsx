import { render, screen, within } from "@testing-library/react";
import { StatsPage } from "../pages/StatsPage";
import type { Stats } from "../types";
import { mockFetch } from "./fixtures";

const STATS: Stats = {
  incidents_indexed: 236,
  triage_requests: 40,
  triage_runs: 10,
  duplicates_suppressed: 30,
  feedback_count: 4,
  feedback_correct: 3,
  feedback_accuracy: 0.75,
  indexed_from_feedback: 1,
  severity_counts: { critical: 3, high: 4, medium: 2, low: 1, info: 0 },
  recent_runs: [
    { id: 10, created_at: "2026-11-19T10:00:00+00:00", model: "demo", severity: "high", summary: "Heap exhausted by unbounded cache", duplicate_count: 5, feedback: "correct" },
    { id: 9, created_at: "2026-11-19T09:00:00+00:00", model: "demo", severity: "low", summary: "Lockfile out of sync", duplicate_count: 0, feedback: null },
  ],
};

describe("StatsPage", () => {
  it("shows totals, reuse share, accuracy and recent runs", async () => {
    mockFetch(() => [200, STATS]);
    render(<StatsPage />);
    expect(await screen.findByText("75%")).toBeInTheDocument();
    expect(screen.getByText("75% of requests, no model call")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 marked correct")).toBeInTheDocument();
    expect(screen.getByText("1 added from feedback")).toBeInTheDocument();
    const runs = within(screen.getByRole("list", { name: "Recent triages" })).getAllByRole("listitem");
    expect(runs).toHaveLength(2);
    expect(runs[0]).toHaveTextContent("seen 6×");
    expect(runs[0]).toHaveTextContent("👍 correct");
    expect(runs[1]).not.toHaveTextContent("seen");
  });

  it("handles no data yet", async () => {
    mockFetch(() => [200, { ...STATS, triage_requests: 0, duplicates_suppressed: 0, feedback_count: 0, feedback_correct: 0, feedback_accuracy: null, recent_runs: [] }]);
    render(<StatsPage />);
    expect(await screen.findByText(/No triages yet/)).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows an error when the API is down", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    render(<StatsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the API");
  });
});
