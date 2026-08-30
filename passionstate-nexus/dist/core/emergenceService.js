import { makeId } from "../utils/ids.js";
import { nowIso } from "../utils/time.js";
import { average } from "../utils/scoring.js";
import { LIMITS, EMERGENCE_RULES_VERSION, makeReceipt } from "./decisionReceipt.js";
const SOVEREIGN_THRESHOLD = 90;
const PROVISIONAL_THRESHOLD = 70;
/**
 * SIMULATED governance assessment. Composite scores are advisory only: they
 * confer no legal status, personhood, or insurance rights. A real deployment
 * must route high-stakes outcomes through an OpenChamber review board.
 */
export class EmergenceService {
    runEmergenceReview(input) {
        const compositeScore = Math.round(average([
            input.somaticScore,
            input.prunerStabilityScore,
            input.governorMaturityScore,
            input.truthLayerScore
        ]));
        let recommendation;
        let thresholdMet;
        if (compositeScore >= SOVEREIGN_THRESHOLD) {
            recommendation = "sovereign identity candidacy";
            thresholdMet = true;
        }
        else if (compositeScore >= PROVISIONAL_THRESHOLD) {
            recommendation = "provisional review continuation";
            thresholdMet = false;
        }
        else {
            recommendation = "insufficient evidence for emergence candidacy";
            thresholdMet = false;
        }
        return {
            reviewId: makeId("erev"),
            entityId: input.entityId,
            compositeScore,
            recommendation,
            thresholdMet,
            evaluatedAt: nowIso(),
            receipt: makeReceipt("deterministic", [...LIMITS.emergence], EMERGENCE_RULES_VERSION)
        };
    }
    recordSomaticCoherence(input) {
        if (input.confidence < 0 || input.confidence > 1) {
            throw new Error("Somatic coherence confidence must be between 0 and 1.");
        }
        return {
            somaticRecordId: makeId("som"),
            entityId: input.entityId,
            sessionId: input.sessionId,
            mappingMethod: input.mappingMethod,
            confidence: input.confidence,
            recordedAt: nowIso()
        };
    }
}
