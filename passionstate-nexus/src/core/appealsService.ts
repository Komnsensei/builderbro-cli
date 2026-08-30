import { makeId } from "../utils/ids.js";
import type { AppealCase } from "../types/domain.js";
import type { FileLicenseAppealInput } from "../schemas/FileLicenseAppealSchema.js";

class AppealsService {
  private cases: Map<string, AppealCase> = new Map();

  file(input: FileLicenseAppealInput): AppealCase {
    const caseId = makeId("apl");
    const newCase: AppealCase = {
      caseId: caseId,
      appellantId: input.appellantId,
      licenseId: input.licenseId,
      reason: input.reason,
      jurisdiction: input.jurisdiction,
      filingDate: new Date().toISOString(),
      status: "filed",
      nextSteps: ["schedule chamber hearing", "submit evidence bundle"]
    };
    this.cases.set(caseId, newCase);
    return newCase;
  }
}

export const appeals = new AppealsService();
export { AppealsService };