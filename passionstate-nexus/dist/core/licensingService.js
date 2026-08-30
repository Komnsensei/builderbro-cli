import { jurisdictionRule } from "../config/jurisdictions.js";
import { makeId } from "../utils/ids.js";
import { nowIso } from "../utils/time.js";
import { ActionableError } from "../utils/errors.js";
const appStore = new Map();
const licenseStore = new Map();
export class LicensingService {
    getRequirements(jurisdiction) {
        const rule = jurisdictionRule(jurisdiction);
        return {
            tier: "learner",
            jurisdiction: rule.code,
            minAge: rule.learnerMinAge,
            guardianConsentUnder: rule.guardianConsentUnder,
            requiredModules: ["VOW-101", "PROMPT-101", "RISK-101", "ARCHIVE-101", "ETHICS-101"],
            practicalAssessment: true,
            supervisedAccess: true
        };
    }
    applyLearner(input) {
        const req = this.getRequirements(input.jurisdiction);
        const missingModules = req.requiredModules.filter(m => !input.educationModuleSet.includes(m));
        if (input.age < req.minAge) {
            throw new ActionableError(`Learner application denied because applicant age ${input.age} is below minimum ${req.minAge} in jurisdiction ${req.jurisdiction}.`, { applicantId: input.applicantId });
        }
        if (req.guardianConsentUnder !== undefined && input.age < req.guardianConsentUnder && !input.guardianConsent) {
            throw new ActionableError(`Learner application incomplete. Guardian consent is required under age ${req.guardianConsentUnder} in jurisdiction ${req.jurisdiction}.`, { missingField: "guardianConsent" });
        }
        if (missingModules.length > 0) {
            throw new ActionableError(`Learner application incomplete. Missing required education modules: ${missingModules.join(", ")}.`, { missingModules });
        }
        const application = {
            applicationId: makeId("app"),
            applicantId: input.applicantId,
            age: input.age,
            jurisdiction: input.jurisdiction,
            status: "eligible",
            submittedAt: nowIso(),
            educationModuleSet: input.educationModuleSet,
            requestedAccessScope: input.requestedAccessScope,
            ...(input.guardianConsent === undefined ? {} : { guardianConsent: input.guardianConsent })
        };
        appStore.set(application.applicationId, application);
        return application;
    }
    getApplication(applicationId) {
        return appStore.get(applicationId);
    }
    issueLicense(input) {
        const license = {
            licenseId: makeId("lic"),
            humanId: input.humanId,
            jurisdiction: input.jurisdiction,
            tier: input.tier,
            endorsements: input.endorsements ?? [],
            status: "active",
            issuedAt: nowIso(),
            ...(input.restrictions === undefined ? {} : { restrictions: input.restrictions })
        };
        licenseStore.set(license.licenseId, license);
        return license;
    }
    getLicense(licenseId) {
        return licenseStore.get(licenseId);
    }
}
