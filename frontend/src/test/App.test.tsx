import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "../App";
import { mockFetch } from "./fixtures";

function renderApp() {
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
}

describe("Health indicator", () => {
  it("shows the incident count and demo mode when healthy", async () => {
    mockFetch((url) => (url.endsWith("/health") ? [200, { status: "ok", db: "up", incidents_indexed: 235, llm_mode: "demo" }] : [200, []]));
    renderApp();
    expect(await screen.findByText("235 incidents indexed")).toBeInTheDocument();
    expect(screen.getByText("Demo mode")).toBeInTheDocument();
  });

  it("says the database is down on a 503", async () => {
    mockFetch((url) => (url.endsWith("/health") ? [503, { status: "degraded", db: "down", incidents_indexed: null, llm_mode: "demo" }] : [200, []]));
    renderApp();
    expect(await screen.findByText("Database down")).toBeInTheDocument();
  });

  it("says the API is offline when it can't be reached", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    renderApp();
    expect(await screen.findByText("API offline")).toBeInTheDocument();
  });
});
