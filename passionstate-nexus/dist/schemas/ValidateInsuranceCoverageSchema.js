import { z } from "zod";
export const ValidateInsuranceCoverageSchema = z.object({
    policyId: z.string().min(1).describe("The unique identifier of the insurance policy."),
    jurisdiction: z.string().min(1).describe("The governing jurisdiction of the insurance policy."),
    coverageType: z.enum(["operator", "embodied_system"]).describe("The type of coverage to validate (e.g., operator, embodied_system).")
});
