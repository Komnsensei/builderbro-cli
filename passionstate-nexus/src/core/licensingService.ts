import { jurisdictionRule } from "../config/jurisdictions.js";
import { makeId } from "../utils/ids.js";
import { nowIso } from "../utils/time.js";
import type { LicenseRecord } from "../types/core.js";
import { ActionableError } from "../utils/errors.js";

type LearnerApplication = {
  applicationId: string;
  applicantId: string;
  age: number;
  jurisdiction: string;
  guardianConsent?: boolean;
  educationModuleSet: string[];
  requestedAccessScope: string;
  status: "submitted" | "eligible" | "ineligible" | "approved";
  submittedAt: string;
};

const appStore = new Map<string, LearnerApplication>();
const licenseStore = new Map<string, LicenseRecord>();

export class LicensingService {
  getRequirements(jurisdiction: string) {
    const rule = jurisdictionRule(jurisdiction);

    return {
      tier: "learner" as const,
      jurisdiction: rule.code,
      minAge: rule.learnerMinAge,
      guardianConsentUnder: rule.guardianConsentUnder,
      requiredModules: ["VOW-101", "PROMPT-101", "RISK-101", "ARCHIVE-101", "ETHICS-101"],
      practicalAssessment: true,
      supervisedAccess: true
    };
  }

  applyLearner(input: {
    applicantId: string;
    age: number;
    jurisdiction: string;
    guardianConsent?: boolean;
    educationModuleSet: string[];
    requestedAccessScope: string;
  }) {
    const req = this.getRequirements(input.jurisdiction);
    const missingModules = req.requiredModules.filter(m => !input.educationModuleSet.includes(m));

    if (input.age < req.minAge) {
      throw new ActionableError(
        `Learner application denied because applicant age ${input.age} is below minimum ${req.minAge} in jurisdiction ${req.jurisdiction}.`,
        { applicantId: input.applicantId }
      );
    }

    if (req.guardianConsentUnder !== undefined && input.age < req.guardianConsentUnder && !input.guardianConsent) {
      throw new ActionableError(
        `Learner application incomplete. Guardian consent is required under age ${req.guardianConsentUnder} in jurisdiction ${req.jurisdiction}.`,
        { missingField: "guardianConsent" }
      );
    }

    if (missingModules.length > 0) {
      throw new ActionableError(
        `Learner application incomplete. Missing required education modules: ${missingModules.join(", ")}.`,
        { missingModules }
      );
    }

    const application: LearnerApplication = {
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

  getApplication(applicationId: string) {
    return appStore.get(applicationId);
  }

  issueLicense(input: {
    humanId: string;
    jurisdiction: string;
    tier: LicenseRecord["tier"];
    endorsements?: string[];
    restrictions?: string[];
  }) {
    const license: LicenseRecord = {
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

  getLicense(licenseId: string) {
    return licenseStore.get(licenseId);
  }
}
