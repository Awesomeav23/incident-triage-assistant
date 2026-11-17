import { errorMessage } from "../api";

describe("errorMessage", () => {
  it("uses a string detail as-is", () => {
    expect(errorMessage(502, { detail: "Claude declined" })).toBe("Claude declined");
  });

  it("turns 422 validation errors into field: message", () => {
    const body = { detail: [{ loc: ["body", "log"], msg: "String should have at least 10 characters" }] };
    expect(errorMessage(422, body)).toBe("log: String should have at least 10 characters");
  });

  it("falls back to a generic message", () => {
    expect(errorMessage(500, null)).toBe("Request failed (HTTP 500)");
    expect(errorMessage(504, "<html>")).toMatch(/unreachable/);
  });
});
