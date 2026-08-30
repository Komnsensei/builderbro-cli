import { z } from "zod";
export const EvaluateConstitutionalGuardSchema = z.object({
    entityId: z.string().min(1).describe("Entity being evaluated"),
    purpose: z.string().min(1).describe("Declared purpose or context of the evaluation"),
    data: z.record(z.any()).optional().describe("Additional evidence relevant to the evaluation")
});
