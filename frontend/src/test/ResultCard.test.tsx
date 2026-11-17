import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResultCard } from "../components/ResultCard";
import { SEVERITIES } from "../types";
import { makeResult } from "./fixtures";

describe("ResultCard", () => {
  it.each(SEVERITIES)("shows the %s severity badge", (severity) => {
    render(<ResultCard result={makeResult({ severity })} />);
    expect(screen.getAllByTestId("severity-badge")[0]).toHaveTextContent(severity);
  });

  it("renders root cause, fix with inline code, and numbered steps", () => {
    render(<ResultCard result={makeResult()} />);
    expect(screen.getByText(/pinned to a node IP/)).toBeInTheDocument();
    expect(screen.getByText("REDIS_HOST").tagName).toBe("CODE");
    const steps = screen.getAllByRole("listitem").filter((li) => li.closest("ol"));
    expect(steps.map((s) => s.textContent)).toEqual(["Check failover events", "Update the config"]);
  });

  it("marks only the incidents Claude cited", () => {
    render(<ResultCard result={makeResult()} />);
    const cited = screen.getAllByTestId("cited");
    expect(cited).toHaveLength(1);
    expect(cited[0].closest("li")).toHaveTextContent("Redis failover");
  });

  it("shows the no-match banner and empty list when nothing cleared the threshold", () => {
    render(<ResultCard result={makeResult({ no_close_match: true, similar_incidents: [], matched_incident_ids: [] })} />);
    expect(screen.getByTestId("no-match")).toBeInTheDocument();
    expect(screen.getByText(/None above the similarity threshold/)).toBeInTheDocument();
  });

  it("tells the user when secrets were redacted", () => {
    render(<ResultCard result={makeResult({ redacted_secrets: 2 })} />);
    expect(screen.getByTestId("redacted")).toHaveTextContent("2 secrets redacted");
  });

  it("expands a similar incident to show its root cause and fix", async () => {
    render(<ResultCard result={makeResult()} />);
    const item = screen.getByText("Redis timeout").closest("button")!;
    expect(screen.queryByText("rc2")).not.toBeInTheDocument();
    await userEvent.click(item);
    expect(within(item.closest("li")!).getByText("rc2")).toBeInTheDocument();
  });

  it("shows confidence and latency", () => {
    render(<ResultCard result={makeResult()} />);
    expect(screen.getByRole("meter", { name: "Confidence" })).toHaveAttribute("aria-valuenow", "82");
    expect(screen.getByText("Total 7.2 s")).toBeInTheDocument();
  });
});
