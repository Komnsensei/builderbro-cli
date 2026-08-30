
export type EmbodiedSystem = {
  systemId: string;
  ownerLicenseId: string;
  deviceClass: string;
  jurisdiction: string;
  registrationDate: string;
  status: "registered" | "pending" | "deregistered" | "suspended";
};

export type InsuranceValidationResult = {
  valid: boolean;
  policyId: string;
  jurisdiction: string;
  coverageType: "operator" | "embodied_system";
  reason: string;
  holderId?: string;
  coverageDetails?: {
    amount: number;
    expiryDate: string;
  };
};

export type AppealCase = {
  caseId: string;
  appellantId: string;
  licenseId: string;
  reason: string;
  jurisdiction: string;
  filingDate: string;
  status: "filed" | "review" | "scheduled" | "adjudicated" | "withdrawn";
  nextSteps: string[];
};