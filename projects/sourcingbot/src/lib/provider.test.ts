/**
 * The provider boundary, proven.
 *
 * Two kinds of test live here. Most assert behaviour. A few assert the ABSENCE
 * of something — that no domain module imports a concrete provider, that no
 * domain module names a website. Absence normally cannot be tested, so it decays
 * silently as a codebase grows: the boundary holds on the day it ships and
 * erodes one reasonable-looking import at a time. These read the source and fail
 * on the erosion, which is the only way a structural rule survives contact with
 * future work.
 *
 * See docs/DECISIONS.md ADR-015, ADR-016, ADR-017.
 */
import { describe, it, expect } from "vitest";
import {
  DEFAULT_PROVIDER_ID,
  PROVIDER_IDS,
  isKnownProvider,
  providerFor,
  registeredProviders,
} from "./provider";
import { ManualProvider } from "./providers/manual";
import {
  OPERATOR_PRESENCE_WINDOW_MS,
  PROHIBITED_CAPABILITIES,
  completeSession,
  confirmOperatorPresence,
  haltSession,
  isActive,
  isSupervisedOrigin,
  operatorPresent,
  pauseSession,
  recordManualCapture,
  recordSkip,
  resumeSession,
  startSession,
  supervisionPolicyFor,
  supportsCapability,
} from "./sourcing-session";
import { migrateWorkspace } from "./store";
import { newCandidate } from "./candidate";
import { seedState } from "./seed";
import type { WorkspaceState } from "./store";
import type { Candidate, SourcingSession } from "./types";

/**
 * Every lib module's source, read at build time by Vite.
 *
 * Uses `import.meta.glob` rather than `node:fs` so the structural tests need no
 * Node type dependency and run in the same jsdom environment as everything else.
 */
const SOURCES = import.meta.glob("./**/*.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const OK = {
  reqId: "req_1",
  operator: "Dana Whitfield",
  acknowledgedPolicy: true,
  reqAcceptsSourcing: true,
};

const started = (over: Partial<SourcingSession> = {}): SourcingSession => ({
  ...startSession(OK),
  ...over,
});

/** Source with line and block comments removed, so prose is not evidence. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

/** Domain modules — everything in lib/ except providers/ and tests. */
function domainModules(): Array<[string, string]> {
  return Object.entries(SOURCES)
    .filter(([p]) => !p.includes("/providers/") && !p.endsWith(".test.ts"))
    .map(([p, src]) => [p, stripComments(src)]);
}

function codeOf(path: string): string {
  const entry = Object.entries(SOURCES).find(([p]) => p === path);
  if (!entry) throw new Error(`No source found for ${path}`);
  return stripComments(entry[1]);
}

// ---------------------------------------------------------------------------
// AC-1 / AC-4 — the boundary is structural, not conventional
// ---------------------------------------------------------------------------

describe("the domain does not depend on any provider", () => {
  it("sees the source it is asserting over", () => {
    // Guards the three tests below: a glob that silently matched nothing would
    // make every structural assertion vacuously pass.
    expect(domainModules().length).toBeGreaterThan(10);
    expect(Object.keys(SOURCES)).toContain("./providers/manual.ts");
  });

  it("no domain module imports a concrete provider", () => {
    const offenders = domainModules()
      .filter(([, code]) => /from\s+"\.\/providers\//.test(code))
      .map(([path]) => path);
    expect(offenders).toEqual([]);
  });

  it("no domain module names a website, beyond three documented tokens", () => {
    // Named rather than pattern-matched, so adding a fourth requires editing
    // this list — which is the point.
    const ALLOWED = [
      "supervised-linkedin", // deprecated origin; retained for load migration
      "linkedInUrl", //         Candidate field; rename deferred, see TASK-0061
      "linkedin-rsc", //        a provider id — naming channels is the registry's job
    ];
    const found: string[] = [];

    for (const [path, code] of domainModules()) {
      for (const match of code.matchAll(/[A-Za-z-]*linkedin[A-Za-z-]*/gi)) {
        if (!ALLOWED.includes(match[0])) found.push(`${path}: ${match[0]}`);
      }
    }
    expect(found).toEqual([]);
  });

  it("provider.ts is inert — it performs no I/O and drives nothing", () => {
    const code = codeOf("./provider.ts");
    expect(code).not.toMatch(/\bfetch\b|XMLHttpRequest|WebSocket|import\s*\(/);
    expect(code).not.toMatch(/node:|document\.|window\./);
  });

  it("no provider module carries a URL, selector, or credential", () => {
    const code = codeOf("./providers/manual.ts");
    expect(code).not.toMatch(/https?:\/\//);
    expect(code).not.toMatch(/querySelector|document\.|cookie|password|token/i);
  });
});

// ---------------------------------------------------------------------------
// AC-5 — the attestation text is unchanged
// ---------------------------------------------------------------------------

describe("ManualProvider preserves today's behaviour", () => {
  /**
   * The exact text that shipped as the global `SUPERVISION_POLICY`. Inlined,
   * not imported: importing it from the provider would make this test assert
   * that a value equals itself. Operators have been signing these words, so
   * they are pinned here independently.
   */
  const SHIPPED_POLICY = [
    "I am initiating and personally supervising this sourcing session.",
    "I will open and review each profile myself; sourcingBOT will not browse for me.",
    "I will record only candidates I have personally reviewed.",
    "I will respect LinkedIn's rate limits and terms; no bypass or evasion.",
  ];

  it("carries the shipped policy text byte for byte", () => {
    expect([...ManualProvider.supervisionPolicy]).toEqual(SHIPPED_POLICY);
  });

  it("does not claim an agent operates the browser", () => {
    expect(ManualProvider.agentOperatesBrowser).toBe(false);
  });

  it("is the only registered provider in this increment", () => {
    expect(registeredProviders().map((p) => p.id)).toEqual(["manual"]);
  });

  it("is what a session's policy resolves to by default", () => {
    expect(supervisionPolicyFor(started())).toEqual(ManualProvider.supervisionPolicy);
  });
});

// ---------------------------------------------------------------------------
// AC-3 — provider attribution
// ---------------------------------------------------------------------------

describe("provider attribution", () => {
  it("stamps the default provider on a new session", () => {
    expect(startSession(OK).providerId).toBe("manual");
    expect(DEFAULT_PROVIDER_ID).toBe("manual");
  });

  it("records an explicitly chosen provider", () => {
    expect(startSession({ ...OK, providerId: "claude-chrome" }).providerId).toBe("claude-chrome");
  });

  it("knows which ids are registered, and reports unknown ones honestly", () => {
    expect(isKnownProvider("manual")).toBe(true);
    // Declared in PROVIDER_IDS but not implemented until a later increment.
    expect(PROVIDER_IDS).toContain("claude-chrome");
    expect(isKnownProvider("claude-chrome")).toBe(false);
    expect(isKnownProvider(undefined)).toBe(false);
  });

  it("falls back to the default rather than crashing on an unknown provider", () => {
    expect(providerFor("something-from-the-future").id).toBe("manual");
  });

  it("every seeded session carries a provider", () => {
    expect(seedState().sessions.every((s) => s.providerId === "manual")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// AC-2 — backward compatibility
// ---------------------------------------------------------------------------

describe("an Increment-1 workspace still loads", () => {
  /** Shaped as it was written before providers or the origin rename existed. */
  const legacy = {
    reqs: [],
    briefs: [],
    reqCandidates: [],
    candidates: [
      { id: "c_1", fullName: "Priya Raman", headline: "", location: "", roles: [], skills: [],
        origin: "supervised-linkedin", notes: "", createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z" },
      { id: "c_2", fullName: "Sam Okafor", headline: "", location: "", roles: [], skills: [],
        origin: "referral", notes: "", createdAt: "2026-01-01T00:00:00Z",
        updatedAt: "2026-01-01T00:00:00Z" },
    ] as Candidate[],
    sessions: [
      { id: "sess_1", reqId: "req_1", operator: "Dana", status: "ended",
        acknowledgedPolicy: true, startedAt: "2026-01-01T00:00:00Z", candidatesAdded: 1,
        notes: "" },
    ] as SourcingSession[],
  } satisfies Partial<WorkspaceState>;

  it("maps the deprecated origin forward", () => {
    const { candidates } = migrateWorkspace(legacy);
    expect(candidates[0].origin).toBe("supervised-session");
  });

  it("leaves other origins untouched", () => {
    expect(migrateWorkspace(legacy).candidates[1].origin).toBe("referral");
  });

  it("defaults a session with no provider to manual", () => {
    expect(migrateWorkspace(legacy).sessions[0].providerId).toBe("manual");
  });

  it("never overwrites a provider that was already recorded", () => {
    const sessions = [{ ...legacy.sessions[0], providerId: "claude-chrome" }];
    expect(migrateWorkspace({ ...legacy, sessions }).sessions[0].providerId).toBe("claude-chrome");
  });

  it("still accepts a legacy-origin person for capture", () => {
    // The person genuinely was supervised; only the spelling changed.
    const legacyPerson = { ...newCandidate({ fullName: "Priya", origin: "referral" }),
      origin: "supervised-linkedin" as const };
    expect(() => recordManualCapture(started(), legacyPerson)).not.toThrow();
    expect(isSupervisedOrigin("supervised-linkedin")).toBe(true);
    expect(isSupervisedOrigin("supervised-session")).toBe(true);
    expect(isSupervisedOrigin("referral")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// AC-8 / AC-9 — halted is not paused
// ---------------------------------------------------------------------------

describe("halted sessions", () => {
  it("records what stopped it, and who saw it", () => {
    const halted = haltSession(started(), {
      reason: "platform-warning",
      detail: "Unusual activity notice",
      raisedBy: "agent",
    });
    expect(halted.status).toBe("halted");
    expect(halted.halts).toHaveLength(1);
    expect(halted.halts?.[0]).toMatchObject({
      reason: "platform-warning",
      raisedBy: "agent",
      detail: "Unusual activity notice",
    });
  });

  it("keeps every halt rather than overwriting — repeated warnings are the signal", () => {
    const once = haltSession(started(), { reason: "checkpoint" });
    const twice = haltSession(resumeSession(once, { acknowledgedPolicy: true }), {
      reason: "rate-limit-notice",
    });
    expect(twice.halts).toHaveLength(2);
  });

  it("is never refused for a live session, whatever its state", () => {
    expect(() => haltSession(pauseSession(started()), { reason: "checkpoint" })).not.toThrow();
    expect(() =>
      haltSession(haltSession(started(), { reason: "checkpoint" }), { reason: "checkpoint" }),
    ).not.toThrow();
  });

  it("refuses only for a session that already concluded", () => {
    expect(() => haltSession(completeSession(started()), { reason: "checkpoint" })).toThrow(
      /completed session cannot be halted/,
    );
  });

  it("still counts as active, so a halt cannot be dodged by opening a second session", () => {
    expect(isActive(haltSession(started(), { reason: "checkpoint" }))).toBe(true);
  });

  it("requires a FRESH acknowledgement to resume", () => {
    const halted = haltSession(started(), { reason: "platform-warning" });
    // The stored acknowledgement from session start is not enough.
    expect(halted.acknowledgedPolicy).toBe(true);
    expect(() => resumeSession(halted)).toThrow(/fresh acknowledgement/);
    expect(resumeSession(halted, { acknowledgedPolicy: true }).status).toBe("in-progress");
  });

  it("resuming from paused is unchanged and needs no fresh acknowledgement", () => {
    expect(resumeSession(pauseSession(started())).status).toBe("in-progress");
  });

  it("can be completed without being resumed first", () => {
    expect(completeSession(haltSession(started(), { reason: "checkpoint" })).status).toBe("ended");
  });
});

// ---------------------------------------------------------------------------
// AC-10 — presence is recorded and testable, not yet enforced
// ---------------------------------------------------------------------------

describe("operator presence", () => {
  const at = (session: SourcingSession, msAfter: number) =>
    new Date(new Date(session.lastOperatorConfirmationAt as string).getTime() + msAfter);

  it("is fifteen minutes", () => {
    expect(OPERATOR_PRESENCE_WINDOW_MS).toBe(15 * 60 * 1000);
  });

  it("treats starting a session as a confirmation", () => {
    const s = startSession(OK);
    expect(s.lastOperatorConfirmationAt).toBeDefined();
    expect(operatorPresent(s, new Date(s.startedAt))).toBe(true);
  });

  it("holds just inside the window and lapses just outside it", () => {
    const s = started();
    expect(operatorPresent(s, at(s, OPERATOR_PRESENCE_WINDOW_MS - 1))).toBe(true);
    expect(operatorPresent(s, at(s, OPERATOR_PRESENCE_WINDOW_MS))).toBe(false);
    expect(operatorPresent(s, at(s, OPERATOR_PRESENCE_WINDOW_MS + 60_000))).toBe(false);
  });

  it("reads absent when nothing was ever confirmed", () => {
    // No evidence anyone is watching must never read as someone watching.
    expect(operatorPresent({ ...started(), lastOperatorConfirmationAt: undefined })).toBe(false);
  });

  it("is refreshed by an explicit confirmation", () => {
    const stale = { ...started(), lastOperatorConfirmationAt: "2026-01-01T00:00:00Z" };
    expect(operatorPresent(stale)).toBe(false);
    expect(operatorPresent(confirmOperatorPresence(stale))).toBe(true);
  });

  it("is NOT enforced in this increment — capture still works with presence lapsed", () => {
    // Enforcement arrives with the MCP surface in TASK-0061. Asserted so the
    // scope line is deliberate rather than an oversight.
    const stale = { ...started(), lastOperatorConfirmationAt: "2026-01-01T00:00:00Z" };
    expect(operatorPresent(stale)).toBe(false);
    expect(() => recordSkip(stale, { name: "Ada", reason: "not a fit" })).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// AC-11 — who proposed, and who decided
// ---------------------------------------------------------------------------

describe("decision attribution", () => {
  it("defaults a skip to the operator, for both roles", () => {
    const s = recordSkip(started(), { name: "Ada Lovelace", reason: "too senior" });
    expect(s.skipped?.[0]).toMatchObject({
      decidedBy: "Dana Whitfield",
      proposedBy: "operator",
    });
  });

  it("records an agent proposal while keeping the human as decider", () => {
    const s = recordSkip(started(), {
      name: "Ada Lovelace",
      reason: "too senior",
      proposedBy: "agent",
    });
    expect(s.skipped?.[0]).toMatchObject({
      decidedBy: "Dana Whitfield",
      proposedBy: "agent",
    });
  });
});

// ---------------------------------------------------------------------------
// AC-12 — the prohibitions are untouched
// ---------------------------------------------------------------------------

describe("the prohibited-capability list is unchanged", () => {
  it("still lists exactly what it listed before", () => {
    expect([...PROHIBITED_CAPABILITIES]).toEqual([
      "unattended-scraping",
      "scheduled-crawling",
      "rate-limit-bypass",
      "automation-evasion",
      "bulk-profile-export",
      "credential-storage",
    ]);
  });

  it("still supports none of them", () => {
    for (const c of PROHIBITED_CAPABILITIES) expect(supportsCapability(c)).toBe(false);
  });
});
