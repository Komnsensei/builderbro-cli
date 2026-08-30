import { z } from "zod";
export const LiabilitySimulationSchema = z.object({ scenarioType: z.string().min(1), humanOperatorId: z.string().min(1), agentId: z.string().min(1), embodiedSystemId: z.string().optional(), emergenceStatus: z.enum(["non-conscious", "under-review", "provisional-subjective", "sovereign"]), faultFactors: z.array(z.string()).default([]) });
