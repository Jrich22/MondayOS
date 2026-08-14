/**
 * Demo workspace data.
 *
 * Synthetic throughout — invented people at invented companies, and sourcing
 * sessions that never took place. Nothing here is derived from a real profile,
 * no seeded candidate carries a LinkedIn URL, and every seeded session is
 * labelled "Demo data" in its own notes, so the demo can never imply that data
 * was captured without supervision or that work was done that was not.
 *
 * The seed loads only when no workspace has been stored yet; the first real
 * save replaces it entirely.
 *
 * The seed deliberately includes ONE person (Priya Raman) attached to TWO
 * requisitions with different stages and different fit scores. That is the
 * clearest demonstration of the central model rule: one Candidate, two
 * ReqCandidates, independent evaluations.
 */
import type {
  Candidate,
  Req,
  ReqCandidate,
  SourcingBrief,
  SourcingSession,
} from "./types";
import type { WorkspaceState } from "./store";
import { DEFAULT_PROVIDER_ID } from "./provider";

const T0 = "2026-08-01T09:00:00.000Z";
const T1 = "2026-08-06T14:30:00.000Z";

function req(
  id: string,
  code: string,
  title: string,
  team: string,
  location: string,
  status: Req["status"],
  hiringManager: string,
  openings = 1,
): Req {
  return {
    id,
    code,
    title,
    team,
    location,
    workModel: "hybrid",
    status,
    hiringManager,
    openings,
    createdAt: T0,
    updatedAt: T1,
  };
}

export function seedState(): WorkspaceState {
  const reqs: Req[] = [
    req("req_infra", "REQ-014", "Staff Platform Engineer", "Infrastructure", "Boston, MA", "open", "Dana Whitfield", 2),
    req("req_ml", "REQ-018", "Senior ML Engineer", "Applied AI", "Remote (US)", "open", "Marcus Ilo"),
    req("req_sec", "REQ-021", "Security Engineer", "Platform Security", "New York, NY", "draft", "Dana Whitfield"),
    req("req_dx", "REQ-009", "Developer Experience Lead", "Infrastructure", "Boston, MA", "closed", "Rae Kimura"),
  ];

  const briefs: SourcingBrief[] = [
    {
      id: "brief_infra",
      reqId: "req_infra",
      version: 3,
      headline: "Platform engineers who have owned multi-tenant infrastructure at scale",
      seniority: "staff",
      requirements: [
        { id: "rq_infra_1", label: "7+ years backend/platform engineering", kind: "required", weight: 5 },
        { id: "rq_infra_2", label: "Owned Kubernetes in production", kind: "required", weight: 4 },
        { id: "rq_infra_3", label: "Multi-tenant SaaS experience", kind: "preferred", weight: 4 },
        { id: "rq_infra_4", label: "Go or Rust", kind: "preferred", weight: 3 },
        { id: "rq_infra_5", label: "Has mentored staff-level engineers", kind: "preferred", weight: 2 },
      ],
      targetCompanies: ["Northwind Cloud", ".Helix Systems", "Arcadia Data"],
      excludedCompanies: ["Lumen Portfolio Co"],
      keywords: ["kubernetes", "platform", "multi-tenant", "terraform"],
      locations: ["Boston, MA", "Remote (US)"],
      outreachAngle:
        "Team is rebuilding the deploy path end to end — high autonomy, no legacy migration debt.",
      createdAt: T0,
      updatedAt: T1,
    },
    {
      id: "brief_ml",
      reqId: "req_ml",
      version: 1,
      headline: "Applied ML engineers shipping inference systems, not research prototypes",
      seniority: "senior",
      requirements: [
        { id: "rq_ml_1", label: "Shipped production ML inference", kind: "required", weight: 5 },
        { id: "rq_ml_2", label: "Python + PyTorch", kind: "preferred", weight: 3 },
        { id: "rq_ml_3", label: "Latency optimization experience", kind: "preferred", weight: 4 },
      ],
      targetCompanies: ["Arcadia Data", "Meridian Labs"],
      excludedCompanies: [],
      keywords: ["inference", "pytorch", "latency", "serving"],
      locations: ["Remote (US)"],
      outreachAngle: "Owns the full serving stack; ships weekly.",
      createdAt: T0,
      updatedAt: T0,
    },
  ];

  const candidates: Candidate[] = [
    {
      id: "cand_priya",
      fullName: "Priya Raman",
      headline: "Staff Infrastructure Engineer",
      location: "Boston, MA",
      email: "priya.raman@example.com",
      roles: [
        { title: "Staff Infrastructure Engineer", company: "Northwind Cloud", startedAt: "2022-03" },
        { title: "Senior SRE", company: "Arcadia Data", startedAt: "2018-06", endedAt: "2022-02" },
      ],
      skills: ["Kubernetes", "Go", "Terraform", "Multi-tenant SaaS"],
      origin: "referral",
      notes: "Referred by Dana. Strong systems depth; interested in platform ownership.",
      createdAt: T0,
      updatedAt: T1,
    },
    {
      id: "cand_tomas",
      fullName: "Tomás Beckett",
      headline: "Principal Engineer, Distributed Systems",
      location: "Remote (US)",
      roles: [{ title: "Principal Engineer", company: "Helix Systems", startedAt: "2020-01" }],
      skills: ["Rust", "Kubernetes", "Distributed systems"],
      origin: "manual-entry",
      notes: "Met at a conference. Not actively looking; revisit in Q4.",
      createdAt: T0,
      updatedAt: T0,
    },
    {
      id: "cand_lena",
      fullName: "Lena Ostrowski",
      headline: "ML Engineer, Inference Platform",
      location: "Remote (US)",
      email: "lena.o@example.com",
      roles: [
        { title: "ML Engineer", company: "Arcadia Data", startedAt: "2021-09" },
        { title: "Data Engineer", company: "Meridian Labs", startedAt: "2019-01", endedAt: "2021-08" },
      ],
      skills: ["PyTorch", "Python", "Model serving", "Latency optimization"],
      origin: "inbound",
      notes: "Applied via careers page; strong serving-stack background.",
      createdAt: T0,
      updatedAt: T0,
    },
    {
      id: "cand_amir",
      fullName: "Amir Haddad",
      headline: "Senior Backend Engineer",
      location: "Boston, MA",
      roles: [{ title: "Senior Backend Engineer", company: "Northwind Cloud", startedAt: "2021-05" }],
      skills: ["Go", "PostgreSQL", "Kubernetes"],
      origin: "manual-entry",
      notes: "Solid, but shy of the staff bar for REQ-014 today.",
      createdAt: T0,
      updatedAt: T0,
    },
  ];

  // Priya appears on BOTH reqs — one person, two independent evaluations.
  const reqCandidates: ReqCandidate[] = [
    {
      id: "rc_priya_infra",
      reqId: "req_infra",
      candidateId: "cand_priya",
      stage: "responded",
      briefVersion: 3,
      assessments: [
        { requirementId: "rq_infra_1", met: "yes", note: "9 years, platform-focused." },
        { requirementId: "rq_infra_2", met: "yes", note: "Owns Northwind's cluster fleet." },
        { requirementId: "rq_infra_3", met: "yes", note: "Northwind is multi-tenant." },
        { requirementId: "rq_infra_4", met: "yes", note: "Go primary." },
        { requirementId: "rq_infra_5", met: "unknown", note: "Not discussed yet." },
      ],
      rationale: "Closest match on multi-tenant ownership; already in Boston.",
      fitScore: 79,
      history: [
        { from: null, to: "identified", at: T0, by: "Dana Whitfield", reason: "Referral" },
        { from: "identified", to: "reviewing", at: T0, by: "Dana Whitfield", reason: "Profile reviewed" },
        { from: "reviewing", to: "contacted", at: T1, by: "Dana Whitfield", reason: "Intro email sent" },
        { from: "contacted", to: "responded", at: T1, by: "Dana Whitfield", reason: "Replied, wants a call" },
      ],
      addedAt: T0,
      updatedAt: T1,
    },
    {
      id: "rc_priya_ml",
      reqId: "req_ml",
      candidateId: "cand_priya",
      stage: "rejected",
      briefVersion: 1,
      assessments: [
        { requirementId: "rq_ml_1", met: "no", note: "Infra depth, no production ML serving." },
      ],
      rationale: "Considered for the ML req; infrastructure profile, not applied ML.",
      fitScore: 0,
      history: [
        { from: null, to: "identified", at: T1, by: "Marcus Ilo", reason: "Cross-req consideration" },
        { from: "identified", to: "rejected", at: T1, by: "Marcus Ilo", reason: "Required ML serving not met" },
      ],
      addedAt: T1,
      updatedAt: T1,
    },
    {
      id: "rc_tomas_infra",
      reqId: "req_infra",
      candidateId: "cand_tomas",
      stage: "reviewing",
      briefVersion: 2,
      assessments: [
        { requirementId: "rq_infra_1", met: "yes", note: "12 years." },
        { requirementId: "rq_infra_2", met: "yes", note: "Helix runs on k8s." },
      ],
      rationale: "Deep distributed-systems background; passive.",
      fitScore: null,
      history: [{ from: null, to: "identified", at: T0, by: "Dana Whitfield", reason: "Conference contact" }],
      addedAt: T0,
      updatedAt: T0,
    },
    {
      id: "rc_amir_infra",
      reqId: "req_infra",
      candidateId: "cand_amir",
      stage: "rejected",
      briefVersion: 3,
      assessments: [{ requirementId: "rq_infra_1", met: "no", note: "5 years; below the required bar." }],
      rationale: "Strong engineer, early for a staff req.",
      fitScore: 0,
      history: [
        { from: null, to: "identified", at: T0, by: "Dana Whitfield", reason: "Internal suggestion" },
        { from: "identified", to: "rejected", at: T0, by: "Dana Whitfield", reason: "Seniority gap" },
      ],
      addedAt: T0,
      updatedAt: T0,
    },
    {
      id: "rc_lena_ml",
      reqId: "req_ml",
      candidateId: "cand_lena",
      stage: "contacted",
      briefVersion: 1,
      assessments: [
        { requirementId: "rq_ml_1", met: "yes", note: "Owns Arcadia's inference path." },
        { requirementId: "rq_ml_2", met: "yes", note: "PyTorch daily." },
        { requirementId: "rq_ml_3", met: "yes", note: "Cut p99 by half." },
      ],
      rationale: "Direct match on serving and latency work.",
      fitScore: 100,
      history: [
        { from: null, to: "identified", at: T0, by: "Marcus Ilo", reason: "Inbound application" },
        { from: "identified", to: "reviewing", at: T0, by: "Marcus Ilo", reason: "Resume reviewed" },
        { from: "reviewing", to: "contacted", at: T1, by: "Marcus Ilo", reason: "Screen scheduled" },
      ],
      addedAt: T0,
      updatedAt: T1,
    },
  ];

  // Two completed sessions so the workspace demonstrates intelligence — capture
  // rates, close calls, skip history — rather than rendering empty modules.
  //
  // SYNTHETIC. These describe sourcing work that never happened, by operators
  // who do not exist, reviewing people who do not exist. Their notes say so in
  // plain text so a session record can never be mistaken for real activity, and
  // the whole seed is discarded the moment a real workspace is stored (see
  // `load()` in store.ts). Nothing here should ever be read as a record of what
  // a recruiter actually did.
  const sessions: SourcingSession[] = [
    {
      id: "sess_infra_1",
      reqId: "req_infra",
      operator: "Dana Whitfield",
      status: "ended",
      acknowledgedPolicy: true,
      startedAt: T1,
      endedAt: T1,
      candidatesAdded: 2,
      notes: "Demo data — synthetic session. First pass on multi-tenant platform profiles.",
      briefVersion: 3,
      capturedCandidateIds: ["cand_priya", "cand_tomas"],
      pauseCount: 1,
      skipped: [
        { id: "skip_1", name: "Marcus Devlin", reason: "Strong, but infra-adjacent rather than infra", closeCall: true, at: T1 },
        { id: "skip_2", name: "Ines Fournier", reason: "Wrong seniority band", closeCall: false, at: T1 },
        { id: "skip_3", name: "Ravi Chandra", reason: "Great systems depth, no multi-tenant", closeCall: true, at: T1 },
      ],
    },
    {
      id: "sess_ml_1",
      reqId: "req_ml",
      operator: "Marcus Ilo",
      status: "ended",
      acknowledgedPolicy: true,
      startedAt: T1,
      endedAt: T1,
      candidatesAdded: 1,
      notes: "Demo data — synthetic session. Inference-focused search.",
      briefVersion: 1,
      capturedCandidateIds: ["cand_lena"],
      pauseCount: 0,
      skipped: [
        { id: "skip_4", name: "Owen Hartley", reason: "Research-only background", closeCall: false, at: T1 },
      ],
    },
  ];

  // Every demo session was a recruiter browsing on their own — stamped in one
  // place rather than repeated on each literal.
  return {
    reqs, briefs, candidates, reqCandidates,
    sessions: sessions.map((s) => ({ ...s, providerId: DEFAULT_PROVIDER_ID })),
  };
}
