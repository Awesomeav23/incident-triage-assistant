import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TriagePage } from "../pages/TriagePage";
import { makeResult, mockFetch } from "./fixtures";

const SAMPLES = [{ name: "Redis failover", log: "redis.exceptions.ConnectionError: Error 111 connecting" }];

describe("TriagePage", () => {
  it("fills the box from a sample and shows the result", async () => {
    const fetch = mockFetch((url) => (url.endsWith("/samples") ? [200, SAMPLES] : [200, makeResult()]));
    render(<TriagePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Redis failover" }));
    expect(screen.getByRole("textbox")).toHaveValue(SAMPLES[0].log);

    await userEvent.click(screen.getByRole("button", { name: "Triage" }));
    expect(await screen.findByTestId("result")).toBeInTheDocument();
    const [, init] = fetch.mock.calls.find(([u]) => String(u).endsWith("/triage"))!;
    expect(JSON.parse(String(init!.body))).toEqual({ log: SAMPLES[0].log, top_k: 3 });
  });

  it("keeps the button disabled until the log is long enough", async () => {
    mockFetch(() => [200, []]);
    render(<TriagePage />);
    const button = screen.getByRole("button", { name: "Triage" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "short");
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), " but now long enough");
    expect(button).toBeEnabled();
  });

  it("shows the API's error message", async () => {
    mockFetch((url) =>
      url.endsWith("/samples") ? [200, []] : [503, { detail: "Claude API credentials are not configured; set ANTHROPIC_API_KEY" }],
    );
    render(<TriagePage />);
    await userEvent.type(screen.getByRole("textbox"), "panic: assignment to entry in nil map");
    await userEvent.click(screen.getByRole("button", { name: "Triage" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("ANTHROPIC_API_KEY"));
  });
});
