import type {
  ArchiveEvent,
  OpenChamber,
  QuantumPass
} from "./core.js";
import type { DecisionReceipt } from "../core/decisionReceipt.js";

export type {
  ArchiveEvent,
  OpenChamber,
  QuantumPass
};

export type EmbodiedSystem = {
  systemId: string;
  ownerLicenseId: string;
  deviceClass: string;
  jurisdiction: string;
  insurancePolicyId?: string;
  operatingScope?: string;
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
  simulated: boolean;
  receipt: DecisionReceipt;
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

export type EndorsementApplication = {
  endorsementApplicationId: string;
  humanId: string;
  endorsementType: "medical" | "legal" | "robotics" | "autonomous-systems";
  jurisdiction: string;
  trainingRecords: string[];
  simulationResults: string[];
  status: "under-review" | "approved" | "rejected";
};

export type TheoryExamResult = {
  examResultId: string;
  applicationId: string;
  examVersion: string;
  score: number;
  passed: boolean;
  remediationTopics: string[];
  retakeEligibleAt?: string;
};

export type PracticalAssessmentResult = {
  assessmentId: string;
  applicationId: string;
  simulationId: string;
  proctorId: string;
  capabilityScore: number;
  complianceScore: number;
  riskFlags: string[];
  passed: boolean;
};

export type EmergenceReviewResult = {
  reviewId: string;
  entityId: string;
  compositeScore: number;
  recommendation: string;
  thresholdMet: boolean;
  evaluatedAt: string;
  receipt: DecisionReceipt;
};

export type SomaticCoherenceRecord = {
  somaticRecordId: string;
  entityId: string;
  sessionId: string;
  mappingMethod: string;
  confidence: number;
  recordedAt: string;
};
