import { LIMITS, INSURANCE_RULES_VERSION, makeReceipt } from "./decisionReceipt.js";
const SIMULATION_VALIDITY_DAYS = 365;
/**
 * SIMULATED insurance provider.
 *
 * Validity is currently derived from the policy ID prefix for demo purposes.
 * A real deployment MUST verify policies against an authorized insurance
 * issuer API and MUST NOT treat any result here as an actual coverage
 * determination. Every response is flagged `simulated: true` so callers and
 * archive records can never mistake this for a real underwriting decision.
 */
class InsuranceService {
    validate(policyId, jurisdiction, coverageType) {
        if (policyId.startsWith("VALID")) {
            const expiryDate = new Date(Date.now() + SIMULATION_VALIDITY_DAYS * 86400000).toISOString();
            return {
                valid: true,
                policyId,
                jurisdiction,
                coverageType,
                holderId: "simulated-holder",
                coverageDetails: { amount: 1000000, expiryDate },
                reason: "Simulated policy accepted (VALID-prefix demo mode).",
                simulated: true,
                receipt: makeReceipt("simulated", [...LIMITS.insurance], INSURANCE_RULES_VERSION)
            };
        }
        return {
            valid: false,
            policyId,
            jurisdiction,
            coverageType,
            reason: "Simulated policy not recognized (only VALID-prefix IDs are accepted in demo mode).",
            simulated: true,
            receipt: makeReceipt("simulated", [...LIMITS.insurance], INSURANCE_RULES_VERSION)
        };
    }
}
export const insurance = new InsuranceService();
