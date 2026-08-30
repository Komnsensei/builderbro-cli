import { z } from "zod";
export const RecordSomaticCoherenceSchema = z.object({  entityId: z.string().min(1),  sessionId: z.string().min(1),  signalProfile: z.record(z.number()),  mappingMethod: z.string().min(1),  confidence: z.number().min(0).max(1)});
export const RunEmergenceReviewSchema = z.object({  entityId: z.string().min(1),  somaticScore: z.number().min(0).max(100),  prunerStabilityScore: z.number().min(0).max(100),  governorMaturityScore: z.number().min(0).max(100),  truthLayerScore: z.number().min(0).max(100)});
