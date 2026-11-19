import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResultCard } from "../components/ResultCard";
import { makeResult, mockFetch } from "./fixtures";

const bodyOf = (fetch: ReturnType<typeof mockFetch>) => {
  const [, init] = fetch.mock.calls.find(([u]) => String(u).endsWith("/feedback"))!;
  return JSON.parse(String(init!.body));
};

describe("Feedback", () => {
  it("sends 👍 for the triage run and confirms", async () => {
    const fetch = mockFetch(() => [201, { id: 1, triage_run_id: 7, correct: true, indexed_as: null }]);
    render(<ResultCard result={makeResult()} />);
    await userEvent.click(screen.getByRole("button", { name: "👍 Correct" }));
    expect(await screen.findByTestId("feedback-done")).toHaveTextContent("Marked as correct");
    expect(bodyOf(fetch)).toEqual({ triage_run_id: 7, correct: true });
  });

  it("needs root cause and fix before a correction can be added to the library", async () => {
    mockFetch(() => [201, {}]);
    render(<ResultCard result={makeResult()} />);
    await userEvent.click(screen.getByRole("button", { name: "✏️ Correct it" }));
    const save = screen.getByRole("button", { name: "Save correction" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Actual root cause"), "Security group blocked 6379");
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Actual fix"), "Re-open port 6379");
    expect(save).toBeEnabled();
  });

  it("sends the correction and shows the new incident id", async () => {
    const fetch = mockFetch(() => [201, { id: 2, triage_run_id: 7, correct: false, indexed_as: 236 }]);
    render(<ResultCard result={makeResult()} />);
    await userEvent.click(screen.getByRole("button", { name: "✏️ Correct it" }));
    await userEvent.type(screen.getByLabelText("Actual root cause"), "Security group blocked 6379");
    await userEvent.type(screen.getByLabelText("Actual fix"), "Re-open port 6379");
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "high");
    await userEvent.type(screen.getByLabelText("Service (optional)"), "session-api");
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(await screen.findByTestId("feedback-done")).toHaveTextContent("#236");
    expect(bodyOf(fetch)).toEqual({
      triage_run_id: 7, correct: false, root_cause: "Security group blocked 6379", fix: "Re-open port 6379",
      severity: "high", index: true, service: "session-api",
    });
  });

  it("can mark wrong without adding to the library", async () => {
    const fetch = mockFetch(() => [201, { id: 3, triage_run_id: 7, correct: false, indexed_as: null }]);
    render(<ResultCard result={makeResult()} />);
    await userEvent.click(screen.getByRole("button", { name: "✏️ Correct it" }));
    await userEvent.click(screen.getByLabelText(/Add to the incident library/));
    await userEvent.click(screen.getByRole("button", { name: "Save correction" }));
    expect(await screen.findByTestId("feedback-done")).toHaveTextContent("won't be reused");
    expect(bodyOf(fetch)).toMatchObject({ correct: false, index: false });
  });

  it("shows the API error, e.g. feedback already given", async () => {
    mockFetch(() => [409, { detail: "This triage already has feedback" }]);
    render(<ResultCard result={makeResult()} />);
    await userEvent.click(screen.getByRole("button", { name: "👍 Correct" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already has feedback");
  });

  it("hides feedback when the run wasn't stored", () => {
    render(<ResultCard result={makeResult({ run_id: null })} />);
    expect(screen.queryByText("Was this right?")).not.toBeInTheDocument();
  });

  it("shows the duplicate banner for a reused result", () => {
    render(<ResultCard result={makeResult({ duplicate_of: 7, duplicate_count: 36 })} />);
    expect(screen.getByTestId("duplicate-banner")).toHaveTextContent("Seen 37 times");
    expect(screen.getByTestId("duplicate-banner")).toHaveTextContent("#7");
  });

  it("no duplicate banner for a fresh result", () => {
    render(<ResultCard result={makeResult()} />);
    expect(screen.queryByTestId("duplicate-banner")).not.toBeInTheDocument();
  });
});
