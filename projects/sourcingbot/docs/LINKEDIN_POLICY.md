# sourcingBOT — LinkedIn Human-Oversight Policy

**This policy is binding on the product, not advisory.** Where it can be
enforced in code it is, and those enforcements are tested.

## The boundary

Every LinkedIn interaction is **initiated and supervised by a named human**.
sourcingBOT records what a recruiter did. It does not browse, fetch, parse, or
act on LinkedIn on anyone's behalf.

## Prohibited — permanently, not "not yet"

| Prohibited | Meaning |
|---|---|
| `unattended-scraping` | Any profile retrieval without a human driving it in real time |
| `scheduled-crawling` | Background jobs, cron, queues that touch LinkedIn |
| `rate-limit-bypass` | Rotating accounts/IPs, request pacing to slip limits |
| `automation-evasion` | Fingerprint spoofing, headless masking, CAPTCHA solving |
| `bulk-profile-export` | Exporting profiles the operator did not individually open |
| `credential-storage` | Storing LinkedIn credentials or session cookies |

These are not deferred features. They are outside the product's definition, and
a future increment that implements one is a policy breach rather than a new
capability.

They are enumerated in code as `PROHIBITED_CAPABILITIES`, and
`supportsCapability()` returns `false` for every one of them — a single greppable
assertion, covered by a parameterized test.

## Enforced rules

### A session cannot exist without a supervising human

`startSession()` **throws** unless all three hold:

1. A **named operator** — a blank name is refused.
2. An explicit **policy acknowledgement** for that session. Not a global setting,
   not a remembered preference: acknowledged each time.
3. The requisition is **open for sourcing** — draft and closed reqs are refused.

The function refuses rather than degrading, because every one of those failures
would otherwise produce a record implying oversight that did not occur.

### Only manual captures are recordable

`recordManualCapture()` accepts an **already-constructed** Candidate. It does not
fetch, parse, or derive profile data — the operator supplies it. Any candidate
whose `origin` is not `supervised-linkedin` is refused, so a bulk import cannot
be laundered through a session to look human-reviewed.

### The boundary is visible, always

The application shell renders the supervision statement on **every** surface, not
buried in settings. A test asserts it is present.

## What operators acknowledge

```
I am initiating and personally supervising this sourcing session.
I will open and review each profile myself; sourcingBOT will not browse for me.
I will record only candidates I have personally reviewed.
I will respect LinkedIn's rate limits and terms; no bypass or evasion.
```

## The deferred workflow

The human-driven LinkedIn workflow — the UI a recruiter uses while sourcing —
lands in a later increment ([ROADMAP](ROADMAP.md), Increment 3). The **gate it
must pass through shipped first, deliberately**: the boundary exists before the
feature that will be tempted to cross it.

When that workflow arrives it will be a *manual capture surface* — the recruiter
browses LinkedIn themselves, in their own browser, and records what they found.
sourcingBOT will never hold the session.

## Data handling

- No seeded record carries a LinkedIn URL, so demo data cannot imply capture.
- `linkedInUrl` is optional and recorded **only** when a human supervised its
  capture.
- Career history is stored at month precision (`YYYY-MM`) — deliberately coarse,
  implying no scraped precision.
- No credentials or cookies are stored. There is no LinkedIn integration to
  authenticate.

## If you are asked to cross this line

The correct response is to decline and escalate. This boundary was approved with
the product (TASK-0052, TASK-0053) and is not an engineering preference that can
be traded away for velocity.
