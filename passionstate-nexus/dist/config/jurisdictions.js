const GLOBAL_RULE = {
    code: "GLOBAL",
    learnerMinAge: 14,
    generalUseMinHistoryMonths: 24,
    guardianConsentUnder: 16,
    requiresInsuranceForEmbodiment: true
};
export const JURISDICTIONS = {
    GLOBAL: GLOBAL_RULE
};
export function jurisdictionRule(code) {
    return JURISDICTIONS[code] ?? GLOBAL_RULE;
}
