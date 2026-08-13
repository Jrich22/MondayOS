/**
 * Demo workspace data.
 *
 * SYNTHETIC THROUGHOUT — invented people at invented companies, requisitions
 * that were never opened, and sourcing sessions that never took place. Nothing
 * here is derived from a real profile, no seeded candidate carries a LinkedIn
 * URL, and every seeded session is labelled "Demo data" in its own notes, so
 * the demo can never imply that data was captured without supervision or that
 * work was done that was not. `isDemoData()` lets any surface say so out loud.
 *
 * The seed loads only when no workspace has been stored yet; the first real
 * save replaces it entirely (see `load()` in store.ts).
 *
 * It is deliberately RICH — six requisitions, sixteen people, eighteen
 * evaluations and six sessions spread over ten weeks — because a dashboard
 * reviewed against three records teaches you nothing about the design. Several
 * people appear on two or three requisitions, which is what makes the
 * persistent-person model visible rather than merely asserted: Priya Raman is
 * `advanced` on REQ-014 and `rejected` on REQ-018, one person with two
 * independent verdicts.
 */
import type {
  Candidate,
  PipelineStage,
  Req,
  ReqCandidate,
  SkippedCandidate,
  SourcingBrief,
  SourcingSession,
  StageEvent,
} from "./types";
import type { WorkspaceState } from "./store";

/** Marker every seeded session carries, so demo data is always identifiable. */
export const DEMO_NOTE_PREFIX = "Demo data — synthetic";

/**
 * True when the workspace is still showing seeded demo content.
 *
 * Detected from the data itself rather than a stored flag: a flag would need
 * clearing correctly on every write path, and getting that wrong would label
 * real recruiter work as demo — or, worse, the reverse.
 */
export function isDemoData(state: {
  sessions: SourcingSession[];
  candidates: Candidate[];
}): boolean {
  if (state.sessions.length === 0) return false;
  return state.sessions.every((s) => s.notes.startsWith(DEMO_NOTE_PREFIX));
}

// Timestamps are relative to now so the demo always looks alive — fixed dates
// would render "last activity: 8 months ago" and make the dashboard read stale.
const daysAgo = (d: number, h = 9): string => {
  const t = new Date();
  t.setDate(t.getDate() - d);
  t.setHours(h, 0, 0, 0);
  return t.toISOString();
};
const hoursAgo = (h: number): string => new Date(Date.now() - h * 3_600_000).toISOString();

// ---------------------------------------------------------------------------
// Requisitions
// ---------------------------------------------------------------------------

interface ReqSpec {
  id: string; code: string; title: string; team: string; location: string;
  status: Req["status"]; hm: string; openings: number; created: number; updated: number;
  jd?: string; intake?: string;
}

const REQ_SPECS: ReqSpec[] = [
  {
    id: "req_infra", code: "REQ-014", title: "Staff Platform Engineer", team: "Infrastructure",
    location: "Boston, MA", status: "open", hm: "Dana Whitfield", openings: 2,
    created: 21, updated: 0,
    jd: "Own the multi-tenant platform end to end: cluster fleet, deploy path, and the internal APIs every product team builds on. You will set technical direction for isolation and tenancy, and mentor the engineers who operate it.",
    intake: "Dana wants depth over breadth — someone who has run multi-tenant infrastructure at real scale, not a generalist. Dealbreaker: no production Kubernetes ownership. Would be impressed by someone who has migrated a single-tenant system to multi-tenant.",
  },
  {
    id: "req_ml", code: "REQ-018", title: "Senior ML Engineer", team: "Applied AI",
    location: "Remote (US)", status: "open", hm: "Marcus Ilo", openings: 1,
    created: 18, updated: 1,
    jd: "Ship inference systems, not research prototypes. You will own the serving stack from model artifact to p99 latency, working directly with the applied research team.",
    intake: "Marcus is explicit that this is not a research role. Wants someone who has cut latency on a live system. Open on domain.",
  },
  {
    id: "req_sec", code: "REQ-021", title: "Security Engineer", team: "Platform Security",
    location: "New York, NY", status: "open", hm: "Dana Whitfield", openings: 1,
    created: 9, updated: 2,
    jd: "Build the security posture for a multi-tenant platform: threat modelling, secrets handling, and the guardrails product teams work inside.",
    intake: "Newly opened. Dana wants someone who has worked alongside platform teams rather than auditing them from outside.",
  },
  {
    id: "req_data", code: "REQ-023", title: "Analytics Engineer", team: "Data",
    location: "Boston, MA", status: "draft", hm: "Rae Kimura", openings: 1,
    created: 4, updated: 3,
    jd: "Own the semantic layer and the models the business actually reports on.",
  },
  {
    id: "req_dx", code: "REQ-009", title: "Developer Experience Lead", team: "Infrastructure",
    location: "Boston, MA", status: "on-hold", hm: "Rae Kimura", openings: 1,
    created: 45, updated: 12,
    jd: "Own the inner loop: build times, local environments, and the tooling engineers touch every day.",
    intake: "On hold pending headcount review.",
  },
  {
    id: "req_mobile", code: "REQ-006", title: "Senior iOS Engineer", team: "Product",
    location: "Remote (US)", status: "closed", hm: "Marcus Ilo", openings: 1,
    created: 70, updated: 30,
  },
];

function buildReqs(): Req[] {
  return REQ_SPECS.map((s) => ({
    id: s.id, code: s.code, title: s.title, team: s.team, location: s.location,
    workModel: (s.location.startsWith("Remote") ? "remote" : "hybrid") as Req["workModel"],
    status: s.status, hiringManager: s.hm, openings: s.openings,
    createdAt: daysAgo(s.created), updatedAt: daysAgo(s.updated),
    ...(s.status === "closed" ? { closedAt: daysAgo(s.updated) } : {}),
    jobDescription: s.jd, intakeNotes: s.intake,
    sourcingGoals: s.status === "open" ? { targetCandidates: 20, targetContacts: 40 } : undefined,
    lastSavedAt: daysAgo(s.updated), rev: 4, savedRev: 4,
  }));
}

// ---------------------------------------------------------------------------
// Briefs
// ---------------------------------------------------------------------------

const rq = (id: string, label: string, kind: "required" | "preferred", weight: number) =>
  ({ id, label, kind, weight });

function buildBriefs(): SourcingBrief[] {
  return [
    {
      id: "brief_infra", reqId: "req_infra", version: 4,
      headline: "Platform engineers who have owned multi-tenant infrastructure at scale",
      seniority: "staff",
      requirements: [
        rq("rq_i1", "7+ years backend/platform engineering", "required", 5),
        rq("rq_i2", "Owned Kubernetes in production", "required", 4),
        rq("rq_i3", "Multi-tenant SaaS experience", "preferred", 5),
        rq("rq_i4", "Go or Rust", "preferred", 3),
        rq("rq_i5", "Has mentored staff-level engineers", "preferred", 2),
      ],
      targetCompanies: ["Northwind Cloud", "Helix Systems", "Arcadia Data", "Basalt Infra"],
      excludedCompanies: ["Lumen Portfolio Co"],
      targetIndustries: ["Developer tools", "Cloud infrastructure"],
      excludedIndustries: ["Defense"],
      keywords: ["kubernetes", "multi-tenant", "terraform", "platform"],
      locations: ["Boston, MA", "Remote (US)"],
      experienceGuidance: "Depth over breadth — eight years on one hard problem beats fifteen across five teams.",
      outreachAngle: "Rebuilding the deploy path end to end — high autonomy, no legacy migration debt.",
      createdAt: daysAgo(21), updatedAt: daysAgo(2),
    },
    {
      id: "brief_ml", reqId: "req_ml", version: 2,
      headline: "Applied ML engineers shipping inference systems, not research prototypes",
      seniority: "senior",
      requirements: [
        rq("rq_m1", "Shipped production ML inference", "required", 5),
        rq("rq_m2", "Python + PyTorch", "preferred", 3),
        rq("rq_m3", "Latency optimization experience", "preferred", 4),
      ],
      targetCompanies: ["Arcadia Data", "Meridian Labs"],
      excludedCompanies: [], targetIndustries: ["AI infrastructure"], excludedIndustries: [],
      keywords: ["inference", "pytorch", "latency", "serving"],
      locations: ["Remote (US)"],
      experienceGuidance: "Serving-stack ownership matters more than model authorship.",
      outreachAngle: "Owns the full serving stack; ships weekly.",
      createdAt: daysAgo(18), updatedAt: daysAgo(6),
    },
    {
      id: "brief_sec", reqId: "req_sec", version: 1,
      headline: "Security engineers who have partnered with platform teams",
      seniority: "senior",
      requirements: [rq("rq_s1", "Application security in a platform org", "required", 5)],
      targetCompanies: [], excludedCompanies: [], targetIndustries: [], excludedIndustries: [],
      keywords: ["appsec", "threat modelling"], locations: ["New York, NY"],
      experienceGuidance: "", outreachAngle: "",
      createdAt: daysAgo(9), updatedAt: daysAgo(9),
    },
    {
      id: "brief_data", reqId: "req_data", version: 1,
      headline: "", seniority: "mid", requirements: [],
      targetCompanies: [], excludedCompanies: [], targetIndustries: [], excludedIndustries: [],
      keywords: [], locations: [], experienceGuidance: "", outreachAngle: "",
      createdAt: daysAgo(4), updatedAt: daysAgo(4),
    },
  ];
}

// ---------------------------------------------------------------------------
// Candidates — the persistent people
// ---------------------------------------------------------------------------

interface PersonSpec {
  id: string; name: string; headline: string; location: string; company: string;
  title: string; since: string; prev?: [string, string, string, string];
  skills: string[]; origin: Candidate["origin"]; email?: string; notes: string; added: number;
}

const PEOPLE: PersonSpec[] = [
  { id: "c_priya", name: "Priya Raman", headline: "Staff Infrastructure Engineer", location: "Boston, MA",
    company: "Northwind Cloud", title: "Staff Infrastructure Engineer", since: "2022-03",
    prev: ["Senior SRE", "Arcadia Data", "2018-06", "2022-02"],
    skills: ["Kubernetes", "Go", "Terraform", "Multi-tenant SaaS"], origin: "referral",
    email: "priya.raman@example.com", notes: "Referred by Dana. Strong systems depth; wants platform ownership.", added: 20 },
  { id: "c_tomas", name: "Tomás Beckett", headline: "Principal Engineer, Distributed Systems", location: "Remote (US)",
    company: "Helix Systems", title: "Principal Engineer", since: "2020-01",
    skills: ["Rust", "Kubernetes", "Distributed systems"], origin: "manual-entry",
    notes: "Met at a conference. Not actively looking; revisit in Q4.", added: 19 },
  { id: "c_lena", name: "Lena Ostrowski", headline: "ML Engineer, Inference Platform", location: "Remote (US)",
    company: "Arcadia Data", title: "ML Engineer", since: "2021-09",
    prev: ["Data Engineer", "Meridian Labs", "2019-01", "2021-08"],
    skills: ["PyTorch", "Python", "Model serving", "Latency optimization"], origin: "inbound",
    email: "lena.o@example.com", notes: "Applied via careers page; strong serving-stack background.", added: 17 },
  { id: "c_amir", name: "Amir Haddad", headline: "Senior Backend Engineer", location: "Boston, MA",
    company: "Northwind Cloud", title: "Senior Backend Engineer", since: "2021-05",
    skills: ["Go", "PostgreSQL", "Kubernetes"], origin: "manual-entry",
    notes: "Solid, but shy of the staff bar today.", added: 16 },
  { id: "c_wei", name: "Wei Zhang", headline: "Staff Engineer, Platform", location: "Boston, MA",
    company: "Basalt Infra", title: "Staff Engineer", since: "2019-11",
    skills: ["Kubernetes", "Go", "Multi-tenant SaaS", "Observability"], origin: "supervised-linkedin",
    notes: "Led the multi-tenant migration at Basalt. Strongest profile seen so far.", added: 12 },
  { id: "c_fatima", name: "Fatima Nasser", headline: "Senior Platform Engineer", location: "Remote (US)",
    company: "Arcadia Data", title: "Senior Platform Engineer", since: "2021-02",
    skills: ["Kubernetes", "Terraform", "Go"], origin: "supervised-linkedin",
    notes: "Deep Terraform work; interested in a step up to staff.", added: 12 },
  { id: "c_jonah", name: "Jonah Pierce", headline: "Infrastructure Engineer", location: "Boston, MA",
    company: "Lumen Portfolio Co", title: "Infrastructure Engineer", since: "2022-08",
    skills: ["Kubernetes", "Python"], origin: "supervised-linkedin",
    notes: "At an excluded company — flagged, kept for future reference.", added: 11 },
  { id: "c_sana", name: "Sana Kapoor", headline: "ML Systems Engineer", location: "Remote (US)",
    company: "Meridian Labs", title: "ML Systems Engineer", since: "2020-06",
    skills: ["PyTorch", "CUDA", "Model serving", "Python"], origin: "supervised-linkedin",
    email: "sana.k@example.com", notes: "Owns Meridian's inference path. Very strong.", added: 10 },
  { id: "c_diego", name: "Diego Marquez", headline: "Senior ML Engineer", location: "Remote (US)",
    company: "Arcadia Data", title: "Senior ML Engineer", since: "2021-04",
    skills: ["Python", "PyTorch", "Latency optimization"], origin: "supervised-linkedin",
    notes: "Colleague of Lena's. Similar profile.", added: 9 },
  { id: "c_hannah", name: "Hannah Wolfe", headline: "Application Security Engineer", location: "New York, NY",
    company: "Helix Systems", title: "Application Security Engineer", since: "2021-01",
    skills: ["AppSec", "Threat modelling", "Go"], origin: "supervised-linkedin",
    notes: "Embedded in Helix's platform team — exactly the partnership model Dana wants.", added: 6 },
  { id: "c_ravi", name: "Ravi Chandra", headline: "Staff Systems Engineer", location: "Boston, MA",
    company: "Helix Systems", title: "Staff Systems Engineer", since: "2018-09",
    skills: ["C++", "Kubernetes", "Distributed systems"], origin: "supervised-linkedin",
    notes: "Great systems depth. No multi-tenant exposure.", added: 12 },
  { id: "c_marcus_d", name: "Marcus Devlin", headline: "Senior Site Reliability Engineer", location: "Remote (US)",
    company: "Northwind Cloud", title: "Senior SRE", since: "2020-03",
    skills: ["Kubernetes", "Terraform", "Incident response"], origin: "supervised-linkedin",
    notes: "Infra-adjacent rather than infra. Worth revisiting if the pipeline thins.", added: 12 },
  { id: "c_elena", name: "Elena Vargas", headline: "Engineering Manager, Platform", location: "Boston, MA",
    company: "Basalt Infra", title: "Engineering Manager", since: "2021-07",
    skills: ["Kubernetes", "Leadership", "Multi-tenant SaaS"], origin: "referral",
    notes: "Referred for DX Lead before it went on hold.", added: 30 },
  { id: "c_oskar", name: "Oskar Lindqvist", headline: "Developer Experience Engineer", location: "Remote (US)",
    company: "Meridian Labs", title: "DX Engineer", since: "2022-02",
    skills: ["Build systems", "Go", "Developer tooling"], origin: "inbound",
    notes: "Inbound for DX Lead. Parked when the req went on hold.", added: 28 },
  { id: "c_nadia", name: "Nadia Okonkwo", headline: "Analytics Engineer", location: "Boston, MA",
    company: "Arcadia Data", title: "Analytics Engineer", since: "2021-11",
    skills: ["dbt", "SQL", "Semantic modelling"], origin: "referral",
    notes: "Referred by Rae for the analytics req once it opens.", added: 3 },
  { id: "c_yusuf", name: "Yusuf Demir", headline: "Senior iOS Engineer", location: "Remote (US)",
    company: "Meridian Labs", title: "Senior iOS Engineer", since: "2019-05",
    skills: ["Swift", "iOS"], origin: "inbound", notes: "Hired outcome recorded on the closed mobile req.", added: 60 },
];

function buildCandidates(): Candidate[] {
  return PEOPLE.map((p) => ({
    id: p.id, fullName: p.name, headline: p.headline, location: p.location,
    email: p.email,
    roles: [
      { title: p.title, company: p.company, startedAt: p.since },
      ...(p.prev ? [{ title: p.prev[0], company: p.prev[1], startedAt: p.prev[2], endedAt: p.prev[3] }] : []),
    ],
    skills: p.skills, origin: p.origin, notes: p.notes,
    createdAt: daysAgo(p.added), updatedAt: daysAgo(Math.max(0, p.added - 2)),
  }));
}

// ---------------------------------------------------------------------------
// ReqCandidates — evaluations. Several people appear on multiple reqs.
// ---------------------------------------------------------------------------

interface EvalSpec {
  id: string; reqId: string; candidateId: string; stage: PipelineStage;
  briefVersion: number; fit: number | null; rationale: string;
  added: number; moves: Array<[PipelineStage, number, string]>;
}

const EVALS: EvalSpec[] = [
  // REQ-014 — the deep pipeline
  { id: "rc_wei_infra", reqId: "req_infra", candidateId: "c_wei", stage: "responded", briefVersion: 4, fit: 92,
    rationale: "Led a single-tenant to multi-tenant migration — exactly the intake ask.", added: 12,
    moves: [["reviewing", 12, "Profile reviewed"], ["contacted", 10, "Intro sent"], ["responded", 8, "Replied, wants a call"]] },
  { id: "rc_priya_infra", reqId: "req_infra", candidateId: "c_priya", stage: "advanced", briefVersion: 4, fit: 88,
    rationale: "Closest match on multi-tenant ownership; already in Boston.", added: 20,
    moves: [["reviewing", 19, "Profile reviewed"], ["contacted", 17, "Intro email sent"], ["responded", 15, "Replied, wants a call"], ["advanced", 5, "Moving to hiring-manager screen"]] },
  { id: "rc_fatima_infra", reqId: "req_infra", candidateId: "c_fatima", stage: "reviewing", briefVersion: 4, fit: 76,
    rationale: "Strong Terraform depth; stepping up to staff.", added: 12, moves: [["reviewing", 11, "Profile reviewed"]] },
  { id: "rc_tomas_infra", reqId: "req_infra", candidateId: "c_tomas", stage: "identified", briefVersion: 2, fit: null,
    rationale: "Deep distributed-systems background; passive.", added: 19, moves: [] },
  { id: "rc_ravi_infra", reqId: "req_infra", candidateId: "c_ravi", stage: "identified", briefVersion: 3, fit: 71,
    rationale: "Excellent systems depth, no multi-tenant exposure.", added: 12, moves: [] },
  { id: "rc_amir_infra", reqId: "req_infra", candidateId: "c_amir", stage: "rejected", briefVersion: 4, fit: 0,
    rationale: "Strong engineer, early for a staff req.", added: 16, moves: [["rejected", 16, "Seniority gap"]] },
  { id: "rc_jonah_infra", reqId: "req_infra", candidateId: "c_jonah", stage: "rejected", briefVersion: 4, fit: 0,
    rationale: "At an excluded company.", added: 11, moves: [["rejected", 11, "Excluded company"]] },

  // REQ-018 — ML
  { id: "rc_sana_ml", reqId: "req_ml", candidateId: "c_sana", stage: "responded", briefVersion: 2, fit: 100,
    rationale: "Owns Meridian's inference path; direct match.", added: 10,
    moves: [["reviewing", 10, "Resume reviewed"], ["contacted", 8, "Screen scheduled"], ["responded", 6, "Keen, scheduling"]] },
  { id: "rc_lena_ml", reqId: "req_ml", candidateId: "c_lena", stage: "contacted", briefVersion: 2, fit: 89,
    rationale: "Cut p99 by half at Arcadia.", added: 17,
    moves: [["reviewing", 16, "Resume reviewed"], ["contacted", 14, "Screen scheduled"]] },
  { id: "rc_diego_ml", reqId: "req_ml", candidateId: "c_diego", stage: "reviewing", briefVersion: 1, fit: 74,
    rationale: "Similar profile to Lena; worth a look.", added: 9, moves: [["reviewing", 9, "Profile reviewed"]] },
  // Cross-req: one person, two independent verdicts.
  { id: "rc_priya_ml", reqId: "req_ml", candidateId: "c_priya", stage: "rejected", briefVersion: 1, fit: 0,
    rationale: "Infrastructure profile, not applied ML.", added: 15,
    moves: [["rejected", 15, "Required ML serving not met"]] },

  // REQ-021 — security, deliberately thin
  { id: "rc_hannah_sec", reqId: "req_sec", candidateId: "c_hannah", stage: "reviewing", briefVersion: 1, fit: 85,
    rationale: "Embedded in Helix's platform team — the partnership model Dana asked for.", added: 6,
    moves: [["reviewing", 5, "Profile reviewed"]] },
  { id: "rc_ravi_sec", reqId: "req_sec", candidateId: "c_ravi", stage: "identified", briefVersion: 1, fit: null,
    rationale: "Systems depth may transfer to platform security.", added: 4, moves: [] },

  // REQ-009 — on hold, historical
  { id: "rc_elena_dx", reqId: "req_dx", candidateId: "c_elena", stage: "contacted", briefVersion: 1, fit: 80,
    rationale: "Referred before the req went on hold.", added: 30,
    moves: [["reviewing", 29, "Reviewed"], ["contacted", 28, "Intro sent"]] },
  { id: "rc_oskar_dx", reqId: "req_dx", candidateId: "c_oskar", stage: "identified", briefVersion: 1, fit: null,
    rationale: "Inbound; parked with the req.", added: 28, moves: [] },
  { id: "rc_wei_dx", reqId: "req_dx", candidateId: "c_wei", stage: "rejected", briefVersion: 1, fit: 0,
    rationale: "Better fit for the platform req.", added: 13, moves: [["rejected", 13, "Redirected to REQ-014"]] },

  // REQ-006 — closed, completed outcome
  { id: "rc_yusuf_mobile", reqId: "req_mobile", candidateId: "c_yusuf", stage: "advanced", briefVersion: 1, fit: 95,
    rationale: "Hired.", added: 60,
    moves: [["reviewing", 59, "Reviewed"], ["contacted", 58, "Screen"], ["responded", 56, "Keen"], ["advanced", 40, "Offer accepted"]] },
];

function buildEvals(): ReqCandidate[] {
  return EVALS.map((e) => {
    const history: StageEvent[] = [
      { from: null, to: "identified", at: daysAgo(e.added), by: "Dana Whitfield", reason: "Added to requisition" },
      ...e.moves.map(([to, d, reason], i): StageEvent => ({
        from: i === 0 ? "identified" : e.moves[i - 1][0],
        to, at: daysAgo(d), by: "Dana Whitfield", reason,
      })),
    ];
    return {
      id: e.id, reqId: e.reqId, candidateId: e.candidateId, stage: e.stage,
      briefVersion: e.briefVersion, assessments: [], rationale: e.rationale,
      fitScore: e.fit, history,
      addedAt: daysAgo(e.added),
      updatedAt: daysAgo(e.moves.length ? e.moves[e.moves.length - 1][1] : e.added),
    };
  });
}

// ---------------------------------------------------------------------------
// Sourcing sessions — varied capture rates so performance ranks meaningfully
// ---------------------------------------------------------------------------

const sk = (id: string, name: string, reason: string, closeCall: boolean, d: number): SkippedCandidate =>
  ({ id, name, reason, closeCall, at: daysAgo(d) });

function buildSessions(): SourcingSession[] {
  return [
    // Strongest: focused search, high capture.
    { id: "sess_infra_2", reqId: "req_infra", operator: "Dana Whitfield", status: "ended",
      acknowledgedPolicy: true, startedAt: daysAgo(12), endedAt: daysAgo(12),
      candidatesAdded: 3, briefVersion: 4, pauseCount: 0,
      capturedCandidateIds: ["c_wei", "c_fatima", "c_ravi"],
      notes: `${DEMO_NOTE_PREFIX} session. Targeted pass on Basalt and Arcadia platform teams.`,
      skipped: [sk("sk_1", "Marcus Devlin", "Infra-adjacent rather than infra", true, 12)] },

    // Weakest: broad search, mostly skips.
    { id: "sess_infra_1", reqId: "req_infra", operator: "Dana Whitfield", status: "ended",
      acknowledgedPolicy: true, startedAt: daysAgo(19), endedAt: daysAgo(19),
      candidatesAdded: 1, briefVersion: 2, pauseCount: 2,
      capturedCandidateIds: ["c_tomas"],
      notes: `${DEMO_NOTE_PREFIX} session. First broad pass — the brief was still loose.`,
      skipped: [
        sk("sk_2", "Ines Fournier", "Wrong seniority band", false, 19),
        sk("sk_3", "Owen Hartley", "No production Kubernetes", false, 19),
        sk("sk_4", "Priyanka Bose", "Strong, but leaving the industry", true, 19),
        sk("sk_5", "Callum Reid", "Contract only", false, 19),
        sk("sk_6", "Sofia Marchetti", "Single-tenant only", false, 19),
      ] },

    { id: "sess_ml_1", reqId: "req_ml", operator: "Marcus Ilo", status: "ended",
      acknowledgedPolicy: true, startedAt: daysAgo(10), endedAt: daysAgo(10),
      candidatesAdded: 2, briefVersion: 2, pauseCount: 1,
      capturedCandidateIds: ["c_sana", "c_diego"],
      notes: `${DEMO_NOTE_PREFIX} session. Inference-focused pass on Meridian and Arcadia.`,
      skipped: [sk("sk_7", "Tobias Lund", "Research-only background", false, 10)] },

    { id: "sess_ml_0", reqId: "req_ml", operator: "Marcus Ilo", status: "ended",
      acknowledgedPolicy: true, startedAt: daysAgo(17), endedAt: daysAgo(17),
      candidatesAdded: 1, briefVersion: 1, pauseCount: 0,
      capturedCandidateIds: ["c_lena"],
      notes: `${DEMO_NOTE_PREFIX} session. Inbound triage.`,
      skipped: [sk("sk_8", "Greta Halvorsen", "Wrong stack", false, 17)] },

    // Live and paused — proves the active-session state renders.
    { id: "sess_sec_1", reqId: "req_sec", operator: "Dana Whitfield", status: "paused",
      acknowledgedPolicy: true, startedAt: hoursAgo(5), pausedAt: hoursAgo(2),
      candidatesAdded: 1, briefVersion: 1, pauseCount: 1,
      capturedCandidateIds: ["c_hannah"],
      notes: `${DEMO_NOTE_PREFIX} session. Security search — paused mid-pass.`,
      skipped: [sk("sk_9", "Aaron Feldman", "Audit background, not embedded", true, 0)] },

    { id: "sess_dx_1", reqId: "req_dx", operator: "Rae Kimura", status: "ended",
      acknowledgedPolicy: true, startedAt: daysAgo(29), endedAt: daysAgo(28),
      candidatesAdded: 1, briefVersion: 1, pauseCount: 0,
      capturedCandidateIds: ["c_oskar"],
      notes: `${DEMO_NOTE_PREFIX} session. Closed out when the req went on hold.`,
      skipped: [] },
  ];
}

export function seedState(): WorkspaceState {
  return {
    reqs: buildReqs(),
    briefs: buildBriefs(),
    candidates: buildCandidates(),
    reqCandidates: buildEvals(),
    sessions: buildSessions(),
  };
}
