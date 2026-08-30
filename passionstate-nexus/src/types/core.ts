export type LicenseTier = "learner" | "general" | "specialized" | "operator";
export type LicenseStatus = "pending" | "active" | "restricted" | "suspended" | "expired";
export type EmergenceStatus = "non-conscious" | "under-review" | "provisional-subjective" | "sovereign";
export type ChamberType = "learner-board" | "appeals-board" | "endorsement-board" | "emergence-review-board";

export type LicenseRecord = {
  licenseId: string;
  humanId: string;
  tier: LicenseTier;
  endorsements: string[];
  jurisdiction: string;
  status: LicenseStatus;
  issuedAt?: string;
  expiresAt?: string;
  restrictions?: string[];
};

export type QuantumPass = {
  quantumPassId: string;
  humanId: string;
  jurisdiction: string;
  participationWeight: number;
  careScore: number;
  complianceScore: number;
  somaticCoherenceScore?: number;
  safeUseHistoryMonths: number;
  endorsements: string[];
  archiveRefs: string[];
  portabilityStatus: "portable" | "restricted" | "jurisdiction-limited";
};

export type ArchiveEvent = {
  archiveEventId: string;
  eventType: string;
  actorIds: string[];
  subjectIds: string[];
  jurisdiction: string;
  timestamp: string;
  payloadHash: string;
  beadRefs: string[];
  payload: unknown;
};

export type OpenChamber = {
  chamberId: string;
  chamberName: string;
  chamberType: ChamberType;
  jurisdictionScope: string[];
  governanceBeadRefs: string[];
  boardMemberIds: string[];
  status: "active" | "paused" | "retired";
};

export type AiEntity = {
  entityId: string;
  displayName: string;
  emergenceStatus: EmergenceStatus;
  prunerStabilityScore?: number;
  governorMaturityScore?: number;
  somaticCoherenceScore?: number;
  sovereignLedgerId?: string;
  archiveRefs: string[];
};