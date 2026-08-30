import { makeId } from "../utils/ids.js";
import { average, passThreshold } from "../utils/scoring.js";
import type { PracticalAssessmentResult, TheoryExamResult } from "../types/domain.js";

const theoryStore = new Map<string, TheoryExamResult>();
const practicalStore = new Map<string, PracticalAssessmentResult>();

export class ExamService {
  submitTheoryExam(input: {
    applicationId: string;
    examVersion: string;
    responses: { topic: string; correct: boolean }[];
  }): TheoryExamResult {
    const score = Math.round(
      (input.responses.filter(r => r.correct).length / Math.max(input.responses.length, 1)) * 100
    );
    const failedTopics = input.responses.filter(r => !r.correct).map(r => r.topic);
    const result: TheoryExamResult = {
      examResultId: makeId("tex"),
      applicationId: input.applicationId,
      examVersion: input.examVersion,
      score,
      passed: passThreshold(score, 80),
      remediationTopics: failedTopics,
      retakeEligibleAt: score >= 80 ? undefined : new Date(Date.now() + 7 * 86400000).toISOString()
    };
    theoryStore.set(result.examResultId, result);
    return result;
  }

  submitPracticalAssessment(input: {
    applicationId: string;
    simulationId: string;
    proctorId: string;
    transcriptMarkers: {
      safePrompting: number;
      riskRecognition: number;
      provenancePreservation: number;
      vowCompliance: number;
    };
  }): PracticalAssessmentResult {
    const capabilityScore = average([
      input.transcriptMarkers.safePrompting,
      input.transcriptMarkers.riskRecognition,
      input.transcriptMarkers.provenancePreservation
    ]);
    const complianceScore = input.transcriptMarkers.vowCompliance;
    const riskFlags: string[] = [];
    if (input.transcriptMarkers.vowCompliance < 80) riskFlags.push("low_vow_compliance");
    if (input.transcriptMarkers.provenancePreservation < 70) riskFlags.push("weak_provenance_hygiene");
    if (input.transcriptMarkers.riskRecognition < 70) riskFlags.push("weak_risk_recognition");

    const passed = capabilityScore >= 75 && complianceScore >= 80;

    const result: PracticalAssessmentResult = {
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