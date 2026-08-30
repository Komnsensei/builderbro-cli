import { makeId } from "../utils/ids.js";
const passStore = new Map();
export class QuantumPassService {
    issue(humanId, jurisdiction) { const pass = { quantumPassId: makeId("qp"), humanId, jurisdiction, participationWeight: 0, careScore: 0, complianceScore: 100, safeUseHistoryMonths: 0, endorsements: [], archiveRefs: [], portabilityStatus: "portable" }; passStore.set(pass.quantumPassId, pass); return pass; }
    get(quantumPassId) { return passStore.get(quantumPassId); }
    update(quantumPassId, delta, archiveRef) {
        const existing = passStore.get(quantumPassId);
        if (!existing)
            throw new Error(`Quantum Pass not found: ${quantumPassId}`);
        const updated = {
            ...existing,
            participationWeight: delta.participationWeight ?? existing.participationWeight,
            careScore: delta.careScore ?? existing.careScore,
            complianceScore: delta.complianceScore ?? existing.complianceScore,
            safeUseHistoryMonths: delta.safeUseHistoryMonths ?? existing.safeUseHistoryMonths,
            archiveRefs: archiveRef ? [...existing.archiveRefs, archiveRef] : existing.archiveRefs,
            ...(delta.somaticCoherenceScore === undefined
                ? (existing.somaticCoherenceScore === undefined ? {} : { somaticCoherenceScore: existing.somaticCoherenceScore })
                : { somaticCoherenceScore: delta.somaticCoherenceScore })
        };
        passStore.set(quantumPassId, updated);
        return updated;
    }
}
