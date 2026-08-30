import { z } from "zod";
export const ValidateInsuranceCoverageSchema = z.object({
  policyId: z.string().min(1).describe("Insurance policy identifier"),
  jurisdiction: z.string().min(1).describe("Governing jurisdiction code"),
  coverageType: z.enum(["operator", "embodied_system"]).describe("Type of coverage to validate")
});
