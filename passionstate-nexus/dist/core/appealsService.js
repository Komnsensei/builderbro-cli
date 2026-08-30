import { makeId } from "../utils/ids.js";
class AppealsService {
    cases = new Map();
    file(input) {
        const caseId = makeId("apl");
        const newCase = {
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
