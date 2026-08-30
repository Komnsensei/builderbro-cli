import { z } from "zod";
export const CreateOpenChamberSchema = z.object({ chamberName: z.string().min(1), chamberType: z.enum(["learner-board", "appeals-board", "endorsement-board", "emergence-review-board"]), jurisdictionScope: z.array(z.string()).min(1), governanceBeadRefs: z.array(z.string()).min(1), boardMemberIds: z.array(z.string()).min(1) });
