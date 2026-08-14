/**
 * The sourcing provider boundary.
 *
 * A *provider* is the channel through which a supervised sourcing session is
 * conducted: the recruiter browsing themselves, an agent operating a browser
 * they are watching, or — one day — a licensed platform integration.
 *
 * This module holds TYPES AND DESCRIPTORS ONLY. It performs no I/O, opens no
 * connection, drives no browser, and knows nothing about any website's markup.
 * That is the entire point: the domain needs to record *which channel* a session
 * used and *what the operator attested to*, and it must be able to do that
 * without depending on how any particular channel works.
 *
 * ## The rule this file exists to enforce
 *
 * Domain modules may import this file. Domain modules may NOT import a concrete
 * provider. `Candidate`, `ReqCandidate` and `SourcingSession` carry a
 * `providerId` string and nothing more — no capability flags, no channel
 * branching, no transport. A new provider is a new descriptor plus a new id; it
 * is never an edit to the model.
 *
 * See docs/DECISIONS.md ADR-015 (policy risk), ADR-016 (host boundary) and
 * ADR-017 (attestation boundary).
 */

/**
 * Stable provider identifiers.
 *
 * These are persisted on every session forever, so they are treated as data,
 * not as a label: renaming one silently reinterprets history. New channels get
 * new ids; existing ids are never repurposed.
 */
export const PROVIDER_IDS = ["manual", "claude-chrome", "linkedin-rsc"] as const;

export type ProviderId = (typeof PROVIDER_IDS)[number];

/**
 * The provider used when none was recorded.
 *
 * Every session written before providers existed was, by construction, a
 * recruiter browsing on their own — there was no other possibility. Defaulting
 * legacy data to `manual` is therefore a statement of fact rather than a guess.
 */
export const DEFAULT_PROVIDER_ID: ProviderId = "manual";

/**
 * A sourcing channel, described.
 *
 * Deliberately inert. A descriptor answers "what did the operator agree to, and
 * does an agent drive the browser?" — questions the domain must be able to ask.
 * It never answers "how do I search", which is not the domain's business.
 */
export interface SourcingProvider {
  id: ProviderId;
  /** Human-facing name, shown when starting a session. */
  label: string;
  /** One line describing how sourcing actually happens on this channel. */
  summary: string;
  /**
   * The acknowledgement text an operator accepts before each session.
   *
   * Per provider rather than global, because the text is an ATTESTATION and it
   * has to be true. The manual wording ("I will open and review each profile
   * myself") stops being true the moment an agent drives the browser, and a
   * signed statement that does not match what happened is precisely the
   * misrepresentation the supervision boundary exists to prevent. See ADR-015.
   */
  supervisionPolicy: readonly string[];
  /**
   * True when something other than the operator's own hands drives the browser.
   *
   * The single capability flag on this interface, and it is here because the
   * *product* changes when it is true — the attestation differs, and presence
   * has to be enforced rather than assumed (ADR-017). Capabilities describing
   * how a channel works belong to the channel, not to this descriptor.
   */
  agentOperatesBrowser: boolean;
}

const REGISTRY = new Map<ProviderId, SourcingProvider>();

/**
 * Register a provider implementation.
 *
 * Providers self-register so the domain never imports one. `provider.ts` is
 * imported by domain modules; `providers/*.ts` are imported by the composition
 * root only, which keeps the dependency arrow pointing one way.
 */
export function registerProvider(provider: SourcingProvider): void {
  REGISTRY.set(provider.id, provider);
}

/** Every registered provider, in registration order. */
export function registeredProviders(): SourcingProvider[] {
  return [...REGISTRY.values()];
}

export class UnknownProviderError extends Error {
  constructor(id: string) {
    super(`No sourcing provider is registered for "${id}".`);
    this.name = "UnknownProviderError";
  }
}

/**
 * Look up a provider, falling back to the default for unknown or missing ids.
 *
 * Falls back rather than throwing because this sits on the read path for every
 * historical session: a workspace written by a future version, or by a build
 * where a provider was not registered, must still render. The fallback is
 * observable — `isKnownProvider` reports it — so nothing silently pretends an
 * unknown channel was manual.
 */
export function providerFor(id: string | undefined): SourcingProvider {
  const found = REGISTRY.get((id ?? DEFAULT_PROVIDER_ID) as ProviderId);
  if (found) return found;
  const fallback = REGISTRY.get(DEFAULT_PROVIDER_ID);
  if (!fallback) throw new UnknownProviderError(id ?? DEFAULT_PROVIDER_ID);
  return fallback;
}

/** True when `id` names a provider that is actually registered. */
export function isKnownProvider(id: string | undefined): boolean {
  return id !== undefined && REGISTRY.has(id as ProviderId);
}

/** Test seam — clears the registry so a test can assert registration itself. */
export function __resetProviders(): void {
  REGISTRY.clear();
}
