import { z } from "zod";

export const LiabilitySimulationSchema = z.object({
  scenarioType: z.string().min(1).describe("The type of scenario to simulate liability for."),
});
