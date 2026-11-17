import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AddIncidentDialog } from "../pages/IncidentsPage";
import { mockFetch } from "./fixtures";

describe("AddIncidentDialog", () => {
  it("lists missing fields instead of posting an incomplete incident", async () => {
    const fetch = mockFetch(() => [201, {}]);
    render(<AddIncidentDialog onClose={() => {}} onAdded={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /save incident/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/title.*service.*error type.*log.*root cause.*fix/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("posts the incident with parsed tags", async () => {
    const fetch = mockFetch(() => [201, { id: 236, title: "DNS flake" }]);
    const onAdded = vi.fn();
    render(<AddIncidentDialog onClose={() => {}} onAdded={onAdded} />);
    const type = (label: RegExp, text: string) => userEvent.type(screen.getByLabelText(label), text);
    await type(/^title/i, "DNS flake");
    await type(/^service/i, "orders-api");
    await type(/^error type/i, "socket.gaierror");
    await type(/log \/ stack trace/i, "socket.gaierror: Errno -3 Temporary failure in name resolution");
    await type(/^root cause/i, "CoreDNS throttled");
    await type(/^fix/i, "Raise CoreDNS CPU");
    await type(/^tags/i, "dns, kubernetes ,");
    await userEvent.click(screen.getByRole("button", { name: /save incident/i }));

    expect(onAdded).toHaveBeenCalledWith("DNS flake");
    const body = JSON.parse(String(fetch.mock.calls[0][1]!.body));
    expect(body).toMatchObject({ title: "DNS flake", severity: "medium", tags: ["dns", "kubernetes"] });
  });
});
