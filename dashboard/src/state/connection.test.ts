import { describe, it, expect } from "vitest";
import { deriveConnection } from "./connection";

describe("deriveConnection", () => {
  it("is connecting while the adapter is loading", () => {
    expect(deriveConnection("loading", true)).toBe("connecting");
  });

  it("is demo on the demo adapter regardless of health", () => {
    expect(deriveConnection("demo", true)).toBe("demo");
    expect(deriveConnection("demo", false)).toBe("demo");
  });

  it("is live when connected and healthy", () => {
    expect(deriveConnection("live", true)).toBe("live");
  });

  it("degrades when live but requests are failing", () => {
    expect(deriveConnection("live", false)).toBe("degraded");
  });
});
