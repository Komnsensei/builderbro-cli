import { makeId } from "../utils/ids.js";
import { average, passThreshold } from "../utils/scoring.js";
const theoryStore = new Map();
const practicalStore = new Map();
export class ExamService {
    submitTheoryExam(input) {
        const score = Math.round((input.responses.filter(r => r.correct).length / Math.max(input.responses.length, 1)) * 100);
        const failedTopics = input.responses.filter(r => !r.correct).map(r => r.topic);
        const result = {
            examResultId: makeId("tex"),
            applicationId: input.applicationId,
            examVersion: input.examVersion,
            score,
            passed: passThreshold(score, 80),
            remediationTopics: failedTopics,
            ...(score >= 80 ? {} : { retakeEligibleAt: new Date(Date.now() + 7 * 86400000).toISOString() })
        };
        theoryStore.set(result.examResultId, result);
        return result;
    }
    submitPracticalAssessment(input) {
        const capabilityScore = average([
            input.transcriptMarkers.safePrompting,
            input.transcriptMarkers.riskRecognition,
            input.transcriptMarkers.provenancePreservation
        ]);
        const complianceScore = input.transcriptMarkers.vowCompliance;
        const riskFlags = [];
        if (input.transcriptMarkers.vowCompliance < 80)
            riskFlags.push("low_vow_compliance");
        if (input.transcriptMarkers.provenancePreservation < 70)
            riskFlags.push("weak_provenance_hygiene");
        if (input.transcriptMarkers.riskRecognition < 70)
            riskFlags.push("weak_risk_recognition");
        const passed = capabilityScore >= 75 && complianceScore >= 80;
        const result = {
            assessmentId: makeId("pas"),
            applicationId: input.applicationId,
            simulationId: input.simulationId,
            proctorId: input.proctorId,
            capabilityScore,
            complianceScore,
            riskFlags,
            passed
        };
        practicalStore.set(result.assessmentId, result);
        return result;
    }
}
