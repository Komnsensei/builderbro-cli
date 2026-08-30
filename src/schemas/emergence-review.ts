import { z } from "zod";

export const RunEmergenceReviewSchema = z.object({
  entityId: z.string().min(1).describe("The ID of the entity to run the emergence review for."),
});
