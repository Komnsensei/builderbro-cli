import { LIMITS, makeReceipt, LIABILITY_RULES_VERSION, } from "./decisionReceipt.js";
/**
 * SIMULATION. Produces a modeled hypothesis about who probably bears
 * responsibility in a scenario. It is not a legal determination, not an
 * insurance decision, and not a court finding — see result.receipt.
 */
export class LiabilityService {
    simulate(input) {
        const operatorFault = input.faultFactors.some((f) => ["poor supervision", "unsafe delegation", "expired license", "no insurance"].includes(f));
        let probableLiableParty = "shared";
        if (input.emergenceStatus === "non-conscious")
            probableLiableParty = input.humanOperatorId;
        else if (input.emergenceStatus === "sovereign" && !operatorFault)
            probableLiableParty = input.agentId;
        else
            probableLiableParty = operatorFault ? input.humanOperatorId : "shared";
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
