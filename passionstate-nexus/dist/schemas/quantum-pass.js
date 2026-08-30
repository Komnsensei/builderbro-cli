import { z } from "zod";
export const IssueQuantumPassSchema = z.object({ humanId: z.string().min(1).describe("Human operator identity"), jurisdiction: z.string().min(1).describe("Jurisdiction binding") });
export const UpdateQuantumPassSchema = z.object({ quantumPassId: z.string().min(1), participationWeight: z.number().optional(), careScore: z.number().optional(), complianceScore: z.number().optional(), somaticCoherenceScore: z.number().optional(), safeUseHistoryMonths: z.number().int().optional(), archiveRef: z.string().optional() });
