import { z } from "zod";
export const FileLicenseAppealSchema = z.object({ licenseId: z.string().min(1), appellantId: z.string().min(1), reason: z.string().min(10), jurisdiction: z.string().min(1) });
