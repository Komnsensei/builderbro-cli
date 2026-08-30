export type JurisdictionRule = {
  code: string;
  learnerMinAge: number;
  generalUseMinHistoryMonths: number;
  guardianConsentUnder?: number;
  requiresInsuranceForEmbodiment: boolean;
};

const GLOBAL_RULE: JurisdictionRule = {
  code: "GLOBAL",
  learnerMinAge: 14,
  generalUseMinHistoryMonths: 24,
  guardianConsentUnder: 16,
  requiresInsuranceForEmbodiment: true
};

export const JURISDICTIONS: Record<string, JurisdictionRule> = {
  GLOBAL: GLOBAL_RULE
};

export function jurisdictionRule(code: string): JurisdictionRule {
  return JURISDICTIONS[code] ?? GLOBAL_RULE;
}
