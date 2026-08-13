/**
 * Provider registration — the composition root for sourcing channels.
 *
 * Importing this module registers every provider the build ships with. It is
 * imported by application entry points and by tests, never by a domain module:
 * that one-way arrow is what keeps `Candidate`, `ReqCandidate` and
 * `SourcingSession` free of any channel dependency.
 */
import { registerProvider } from "../provider";
import { ManualProvider } from "./manual";

registerProvider(ManualProvider);

export { ManualProvider };
