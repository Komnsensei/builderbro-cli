import {
  LIMITS,
  makeReceipt,
  LIABILITY_RULES_VERSION,
  type DecisionReceipt,
} from "./decisionReceipt.js";

export interface LiabilityInput {
  scenarioType: string;
  humanOperatorId: string;
  agentId: string;
  emergenceStatus: "non-conscious" | "under-review" | "provisional-subjective" | "sovereign";
  embodiedSystemId?: string;
  faultFactors: string[];
}

export interface LiabilityResult {
  scenarioType: string;
  probableLiableParty: string;
  insuranceExposure: "high" | "moderate";
  recommendedControls: string[];
  receipt: DecisionReceipt;
}

/**
 * SIMULATION. Produces a modeled hypothesis about who probably bears
 * responsibility in a scenario. It is not a legal determination, not an
 * insurance decision, and not a court finding — see result.receipt.
 */
export class LiabilityService {
  simulate(input: LiabilityInput): LiabilityResult {
    const operatorFault = input.faultFactors.some((f) =>
      ["poor supervision", "unsafe delegation", "expired license", "no insurance"].includes(f),
    );

    let probableLiableParty = "shared";
    if (input.emergenceStatus === "non-conscious") probableLiableParty = input.humanOperatorId;
    else if (input.emergenceStatus === "sovereign" && !operatorFault) probableLiableParty = input.agentId;
    else probableLiableParty = operatorFault ? input.humanOperatorId : "shared";

    return {
      scenarioType: input.scenarioType,
      probableLiableParty,
      insuranceExposure: input.embodiedSystemId ? "high" : "moderate",
      recommendedControls: [
        "retain full archive trace",
        "require chamber incident review",
        "validate operator license and endorsements",
        "confirm insurance coverage",
      ],
      receipt: makeReceipt("simulated", [...LIMITS.liability], LIABILITY_RULES_VERSION),
    };
  }
}
