import { z } from "zod";
export const FileLicenseAppealSchema = z.object({
    appellantId: z.string().min(1).describe("The ID of the individual or entity filing the appeal."),
    licenseId: z.string().min(1).describe("The ID of the license being appealed."),
    reason: z.string().min(1).describe("The detailed reason for the appeal."),
    jurisdiction: z.string().min(1).describe("The governing jurisdiction for the appeal.")
});
