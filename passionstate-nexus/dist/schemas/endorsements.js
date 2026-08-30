import { z } from "zod";
export const ApplySpecializedEndorsementSchema = z.object({ humanId: z.string().min(1), endorsementType: z.enum(["medical", "legal", "robotics", "autonomous-systems"]), jurisdiction: z.string().min(1), trainingRecords: z.array(z.string()).min(1), simulationResults: z.array(z.string()).min(1) });
