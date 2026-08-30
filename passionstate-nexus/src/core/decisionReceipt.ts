/**
 * Decision receipts: every determination this service emits is stamped with
 * what produced it, under which rules, and what it is NOT.
 *
 * Credibility contract:
 *  - No determination is ever returned without a receipt.
 *  - "mode" tells the consumer whether the result is deterministic
 *    (replayable, auditable) or simulated (illustrative only).
 *  - "limitations" always names the missing real-world dependencies so a
 *    reviewer can see exactly what stands between this output and a
 *    binding determination.
 */
import { makeId } from "../utils/ids.js";
import { nowIso } from "../utils/time.js";

export type ComputationMode = "deterministic" | "simulated";

export interface DecisionReceipt {
  receiptId: string;
  /** Semantic version of the decision rules that produced this result. */
  ruleVersion: string;
  /** UTC instant the determination was computed. */
  computedAt: string;
  mode: ComputationMode;
  /** Explicit statement of what this result is NOT. */
  limitations: string[];
}

export const LIABILITY_RULES_VERSION = "liability-rules@0.1.0";
export const INSURANCE_RULES_VERSION = "insurance-rules@0.1.0";
export const EMERGENCE_RULES_VERSION = "emergence-rules@0.1.0";

export function makeReceipt(mode: ComputationMode, limitations: readonly string[], ruleVersion: string): DecisionReceipt {
  return {
    receiptId: makeId("drc"),
    ruleVersion,
    computedAt: nowIso(),
    mode,
    limitations: [...limitations],
  };
}

/** Shared limitation text so wording stays consistent across services. */
export const LIMITS = {
  liability: [
    "decision-support simulation only; not legal advice",
    "not an insurance determination or claim decision",
    "probableLiableParty is a modeled hypothesis, not a court finding",
    "real determinations require a licensed adjuster and governing law review",
  ],
  insurance: [
    "policy data is SIMULATED; no real insurer was contacted",
    "not proof of coverage; verify against the issuing carrier before relying on it",
    "coverage terms shown are illustrative defaults",
  ],
  emergence: [
    "research instrument, not a consciousness or personhood finding",
    "scores are self-reported or system-internal; no independent evaluation was performed",
    "must not be used to create legal personhood or liability rights",
  ],
} as const;
