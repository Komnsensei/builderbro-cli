export type JurisdictionRule = {
  code: string;
  learnerMinAge: number;
  generalUseMinHistoryMonths: number;
  guardianConsentUnder?: number;
  requiresInsuranceForEmbodiment: boolean;
};

export const JURISDICTIONS: Record<string, JurisdictionRule> = {
  GLOBAL: {
    code: "GLOBAL",
    learnerMinAge: 14,
    generalUseMinHistoryMonths: 24,
    guardianConsentUnder: 16,
    requiresInsuranceForEmbodiment: true
  }
};