import type { InsuranceValidationResult } from "../types/domain.js";

class InsuranceService {
  validate(policyId: string, jurisdiction: string, coverageType: "operator" | "embodied_system"): InsuranceValidationResult {
    console.log(`Validating insurance policy ${policyId} for ${coverageType} in ${jurisdiction}`);

    if (policyId.startsWith("VALID")) {
      return {
        valid: true,
        policyId: policyId,
        jurisdiction: jurisdiction,
        coverageType: coverageType,
        holderId: "mock-holder-123",
        coverageDetails: { amount: 1000000, expiryDate: "2025-12-31" },
        reason: "Policy is active and covers specified type."
      };
    } else {
      return {
        valid: false,
        policyId: policyId,
        jurisdiction: jurisdiction,
        coverageType: coverageType,
        reason: "Policy not found or not active for specified coverage."
      };
    }
  }
}

export const insurance = new InsuranceService();