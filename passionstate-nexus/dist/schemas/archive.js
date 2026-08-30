import { z } from "zod";
export const QueryArchiveSchema = z.object({ eventType: z.string().optional(), actorId: z.string().optional(), subjectId: z.string().optional(), jurisdiction: z.string().optional(), page: z.number().int().min(1).optional(), pageSize: z.number().int().min(1).max(100).optional() });
