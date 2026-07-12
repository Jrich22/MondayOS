import { describe, it, expect } from "vitest";
import { applyDecision, canDecide, decisionLabel } from "./approvals";
import type { Approval } from "@/adapter/types";

const open = (): Approval => ({
  id: "AP", taskId: "T", teamRunId: "TR", summary: "", status: "open", verdicts: [], affected: [],
});

describe("approval flow guards", () => {
  it("only open approvals can be decided", () => {
    expect(canDecide({ status: "open" })).toBe(true);
    expect(canDecide({ status: "approved" })).toBe(false);
    expect(canDecide({ status: "rejected" })).toBe(false);
  });

  it("applies an approve decision", () => {
    const { approval, duplicate } = applyDecision(open(), "approve");
    expect(approval.status).toBe("approved");
    expect(duplicate).toBe(false);
  });

  it("applies a reject decision", () => {
    const { approval } = applyDecision(open(), "reject");
    expect(approval.status).toBe("rejected");
  });

  it("guards against duplicate approvals gracefully", () => {
    const once = applyDecision(open(), "approve").approval;
    const twice = applyDecision(once, "approve");
    expect(twice.duplicate).toBe(true);
    expect(twice.approval.status).toBe("approved"); // unchanged
  });

  it("labels decisions", () => {
    expect(decisionLabel("approved")).toBe("Approved");
    expect(decisionLabel("open")).toBe("Awaiting review");
  });
});
