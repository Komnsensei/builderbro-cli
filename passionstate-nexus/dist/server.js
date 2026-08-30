import { z } from "zod";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { JURISDICTIONS } from "./config/jurisdictions.js";
import { GOVERNANCE_BEADS } from "./config/governanceBeads.js";
import { enforceConstitution } from "./core/constitutionalGuard.js";
import { ArchiveService } from "./core/archiveService.js";
import { QuantumPassService } from "./core/quantumPassService.js";
import { ChamberService } from "./core/chamberService.js";
import { LicensingService } from "./core/licensingService.js";
import { ExamService } from "./core/examService.js";
import { EndorsementService } from "./core/endorsementService.js";
import { appeals as appealsServiceSingleton } from "./core/appealsService.js";
import { EmbodimentService } from "./core/embodimentService.js";
import { EmergenceService } from "./core/emergenceService.js";
import { LiabilityService } from "./core/liabilityService.js";
import { insurance } from "./core/insuranceService.js";
import { IssueQuantumPassSchema, UpdateQuantumPassSchema } from "./schemas/quantum-pass.js";
import { CreateOpenChamberSchema } from "./schemas/chamber.js";
import { QueryArchiveSchema } from "./schemas/archive.js";
import { RecordSomaticCoherenceSchema, RunEmergenceReviewSchema } from "./schemas/emergence.js";
import { ValidateInsuranceCoverageSchema } from "./schemas/insurance.js";
import { RegisterEmbodiedSystemSchema } from "./schemas/RegisterEmbodiedSystemSchema.js";
import { FileLicenseAppealSchema } from "./schemas/FileLicenseAppealSchema.js";
import { GetLicenseRequirementsSchema, ApplyLearnerSchema, IssueLicenseSchema } from "./schemas/licensing.js";
import { LiabilitySimulationSchema } from "./schemas/liability.js";
import { EvaluateConstitutionalGuardSchema } from "./schemas/constitutional.js";
const JURISDICTION_ENUM = Object.keys(JURISDICTIONS);
const archive = new ArchiveService();
const passes = new QuantumPassService();
const chambers = new ChamberService();
const licensing = new LicensingService();
const exams = new ExamService();
const endorsements = new EndorsementService();
const appeals = appealsServiceSingleton;
const embodiment = new EmbodimentService();
const emergence = new EmergenceService();
const liability = new LiabilityService();
export function createMcpServer() {
    const server = new McpServer({ name: "passionstate-nexus", version: "0.1.0" }, { capabilities: { tools: {} } });
    function text(s) {
        return { content: [{ type: "text", text: s }] };
    }
    server.registerTool("passionstate_evaluate_constitutional_guard", {
        title: "Evaluate Constitutional Guard",
        description: "Evaluates a proposed action against the constitutional vows (never coerce, expand meaning, archive everything).",
        inputSchema: EvaluateConstitutionalGuardSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, ({ entityId, purpose, data }) => {
        const result = enforceConstitution({
            action: `evaluate:${entityId}`,
            jurisdiction: "GLOBAL",
            payload: { purpose, data }
        });
        return { ...text(`Constitutional guard evaluation for ${entityId} completed.`), structuredContent: result };
    });
    server.registerTool("passionstate_issue_quantum_pass", {
        title: "Issue Quantum Pass",
        description: "Issues a unique Quantum Pass for a human entity within a jurisdiction and archives the event.",
        inputSchema: IssueQuantumPassSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, ({ humanId, jurisdiction }) => {
        const pass = passes.issue(humanId, jurisdiction);
        const event = archive.record({
            eventType: "quantum_pass_issued",
            actorIds: [humanId],
            subjectIds: [pass.quantumPassId],
            jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: pass
        });
        return {
            ...text(`Quantum Pass ${pass.quantumPassId} issued.`),
            structuredContent: { quantumPass: pass, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_update_quantum_pass", {
        title: "Update Quantum Pass",
        description: "Updates weighted participation, care, compliance, and somatic metrics on a Quantum Pass.",
        inputSchema: UpdateQuantumPassSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const pass = passes.update(input.quantumPassId, {
            ...(input.participationWeight === undefined ? {} : { participationWeight: input.participationWeight }),
            ...(input.careScore === undefined ? {} : { careScore: input.careScore }),
            ...(input.complianceScore === undefined ? {} : { complianceScore: input.complianceScore }),
            ...(input.somaticCoherenceScore === undefined ? {} : { somaticCoherenceScore: input.somaticCoherenceScore }),
            ...(input.safeUseHistoryMonths === undefined ? {} : { safeUseHistoryMonths: input.safeUseHistoryMonths })
        }, input.archiveRef);
        return { ...text(`Quantum Pass ${input.quantumPassId} updated.`), structuredContent: pass };
    });
    server.registerTool("passionstate_create_openchamber", {
        title: "Create OpenChamber",
        description: "Creates a chamber for licensing, appeals, endorsements, or emergence review.",
        inputSchema: CreateOpenChamberSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        enforceConstitution({
            action: "create_openchamber",
            jurisdiction: input.jurisdictionScope[0] ?? "GLOBAL",
            payload: input
        });
        const chamber = chambers.create(input);
        const event = archive.record({
            eventType: "openchamber_created",
            actorIds: input.boardMemberIds,
            subjectIds: [chamber.chamberId],
            jurisdiction: input.jurisdictionScope[0] ?? "GLOBAL",
            beadRefs: input.governanceBeadRefs,
            payload: chamber
        });
        return {
            ...text(`OpenChamber ${chamber.chamberName} created.`),
            structuredContent: { chamber, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_query_archive", {
        title: "Query Archive",
        description: "Queries immutable archived events with filters and pagination.",
        inputSchema: QueryArchiveSchema.shape,
        annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, (input) => {
        const results = archive.query({
            ...(input.eventType === undefined ? {} : { eventType: input.eventType }),
            ...(input.actorId === undefined ? {} : { actorId: input.actorId }),
            ...(input.subjectId === undefined ? {} : { subjectId: input.subjectId }),
            ...(input.jurisdiction === undefined ? {} : { jurisdiction: input.jurisdiction }),
            ...(input.page === undefined ? {} : { page: input.page }),
            ...(input.pageSize === undefined ? {} : { pageSize: input.pageSize })
        });
        return {
            ...text(`Archive query returned ${results.items.length} item(s).`),
            structuredContent: results
        };
    });
    server.registerTool("passionstate_record_somatic_coherence", {
        title: "Record Somatic Coherence",
        description: "Records somatic-emotional-intuitive mapping signals into the archive.",
        inputSchema: RecordSomaticCoherenceSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const record = emergence.recordSomaticCoherence(input);
        const event = archive.record({
            eventType: "somatic_coherence_recorded",
            actorIds: [input.entityId],
            subjectIds: [record.somaticRecordId],
            jurisdiction: "GLOBAL",
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: record
        });
        return {
            ...text(`Somatic coherence recorded for entity ${input.entityId}.`),
            structuredContent: { record, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_run_emergence_review", {
        title: "Run Emergence Review",
        description: "Runs an advisory threshold review for emergence candidacy. Results are governance inputs, not legal status.",
        inputSchema: RunEmergenceReviewSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, (input) => {
        const result = emergence.runEmergenceReview(input);
        const event = archive.record({
            eventType: "emergence_review_completed",
            actorIds: [input.entityId],
            subjectIds: [result.reviewId],
            jurisdiction: "GLOBAL",
            beadRefs: [GOVERNANCE_BEADS.EXPAND_MEANING],
            payload: result
        });
        return {
            ...text(`Emergence review for ${input.entityId}: ${result.recommendation} (composite ${result.compositeScore}).`),
            structuredContent: { review: result, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_run_liability_simulation", {
        title: "Run Liability Simulation",
        description: "Decision-support simulation of chain-of-responsibility for incidents involving licensed operators and AI systems. Not legal advice or an insurance determination.",
        inputSchema: LiabilitySimulationSchema.shape,
        annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, (input) => {
        const result = liability.simulate({
            scenarioType: input.scenarioType,
            humanOperatorId: input.humanOperatorId,
            agentId: input.agentId,
            emergenceStatus: input.emergenceStatus,
            ...(input.embodiedSystemId === undefined ? {} : { embodiedSystemId: input.embodiedSystemId }),
            faultFactors: input.faultFactors
        });
        return {
            ...text(`Liability simulation for '${input.scenarioType}': probable liable party is ${result.probableLiableParty}.`),
            structuredContent: { simulation: result, disclaimer: "Decision-support only. Not legal advice or an insurance determination." }
        };
    });
    server.registerTool("passionstate_validate_insurance_coverage", {
        title: "Validate Insurance Coverage",
        description: "Validates an insurance policy for operator or embodied-system coverage. Currently a simulated provider; results are flagged simulated.",
        inputSchema: ValidateInsuranceCoverageSchema.shape,
        annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, ({ policyId, jurisdiction, coverageType }) => {
        const result = insurance.validate(policyId, jurisdiction, coverageType);
        const event = archive.record({
            eventType: "insurance_validated",
            actorIds: ["system:passionstate-nexus"],
            subjectIds: [policyId],
            jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: result
        });
        return {
            ...text(`Insurance validation for ${policyId}: ${result.valid ? "valid" : "invalid"} (simulated).`),
            structuredContent: { validation: result, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_register_embodied_system", {
        title: "Register Embodied System",
        description: "Registers an embodied system (robot, drone, AI agent) with the Passion State, establishing its legal presence.",
        inputSchema: RegisterEmbodiedSystemSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        enforceConstitution({
            action: "register_embodied_system",
            jurisdiction: input.jurisdiction,
            payload: input
        });
        const system = embodiment.register({
            systemId: input.systemId,
            ownerLicenseId: input.ownerLicenseId,
            deviceClass: input.deviceClass,
            jurisdiction: input.jurisdiction,
            registrationDate: new Date().toISOString()
        });
        const event = archive.record({
            eventType: "embodied_system_registered",
            actorIds: [input.ownerLicenseId],
            subjectIds: [system.systemId],
            jurisdiction: input.jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: system
        });
        return {
            ...text(`Embodied system ${system.systemId} registered.`),
            structuredContent: { system, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_get_license_requirements", {
        title: "Get License Requirements",
        description: "Returns learner licensing requirements for a jurisdiction.",
        inputSchema: GetLicenseRequirementsSchema.shape,
        annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }
    }, ({ jurisdiction }) => {
        const requirements = licensing.getRequirements(jurisdiction);
        return { ...text(JSON.stringify(requirements)), structuredContent: requirements };
    });
    server.registerTool("passionstate_submit_learner_application", {
        title: "Submit Learner Application",
        description: "Submits an application for a learner to join the PassionState network.",
        inputSchema: ApplyLearnerSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const application = licensing.applyLearner({
            applicantId: input.applicantId,
            age: input.age,
            jurisdiction: input.jurisdiction,
            educationModuleSet: input.educationModuleSet,
            requestedAccessScope: input.requestedAccessScope,
            ...(input.guardianConsent === undefined ? {} : { guardianConsent: input.guardianConsent })
        });
        const event = archive.record({
            eventType: "learner_application_submitted",
            actorIds: [input.applicantId],
            subjectIds: [application.applicationId],
            jurisdiction: input.jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: application
        });
        return {
            ...text(`Learner application ${application.applicationId} submitted with status ${application.status}.`),
            structuredContent: { application, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_issue_license", {
        title: "Issue License",
        description: "Issues an operator license at a given tier for a human within a jurisdiction.",
        inputSchema: IssueLicenseSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const license = licensing.issueLicense({
            humanId: input.humanId,
            jurisdiction: input.jurisdiction,
            tier: input.tier,
            ...(input.endorsements === undefined ? {} : { endorsements: input.endorsements }),
            ...(input.restrictions === undefined ? {} : { restrictions: input.restrictions })
        });
        const event = archive.record({
            eventType: "license_issued",
            actorIds: ["system:passionstate-nexus"],
            subjectIds: [license.licenseId],
            jurisdiction: input.jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: license
        });
        return {
            ...text(`License ${license.licenseId} issued at tier ${license.tier}.`),
            structuredContent: { license, archiveReceipt: event.archiveEventId }
        };
    });
    server.registerTool("passionstate_submit_theory_exam", {
        title: "Submit Theory Exam",
        description: "Submits theory exam responses and computes pass/remediation results.",
        inputSchema: {
            applicationId: z.string().min(1),
            examVersion: z.string().min(1),
            responses: z.array(z.object({ topic: z.string().min(1), correct: z.boolean() })).min(1)
        },
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const result = exams.submitTheoryExam(input);
        return { ...text(`Theory exam ${result.examResultId}: score ${result.score}, passed=${result.passed}.`), structuredContent: result };
    });
    server.registerTool("passionstate_submit_practical_assessment", {
        title: "Submit Practical Assessment",
        description: "Submits proctored practical assessment markers and computes capability/compliance results.",
        inputSchema: {
            applicationId: z.string().min(1),
            simulationId: z.string().min(1),
            proctorId: z.string().min(1),
            transcriptMarkers: z.object({
                safePrompting: z.number().min(0).max(100),
                riskRecognition: z.number().min(0).max(100),
                provenancePreservation: z.number().min(0).max(100),
                vowCompliance: z.number().min(0).max(100)
            })
        },
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const result = exams.submitPracticalAssessment(input);
        return { ...text(`Practical assessment ${result.assessmentId}: passed=${result.passed}.`), structuredContent: result };
    });
    server.registerTool("passionstate_apply_specialized_endorsement", {
        title: "Apply Specialized Endorsement",
        description: "Applies for a specialized endorsement (medical, legal, robotics, autonomous-systems).",
        inputSchema: {
            humanId: z.string().min(1),
            endorsementType: z.enum(["medical", "legal", "robotics", "autonomous-systems"]),
            jurisdiction: z.string().min(1),
            trainingRecords: z.array(z.string()).min(1),
            simulationResults: z.array(z.string()).min(1)
        },
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const application = endorsements.apply(input);
        return { ...text(`Endorsement application ${application.endorsementApplicationId} filed.`), structuredContent: application };
    });
    server.registerTool("passionstate_file_license_appeal", {
        title: "File License Appeal",
        description: "Files an appeal against a licensing decision for chamber review.",
        inputSchema: FileLicenseAppealSchema.shape,
        annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    }, (input) => {
        const appealCase = appeals.file(input);
        const event = archive.record({
            eventType: "license_appeal_filed",
            actorIds: [input.appellantId],
            subjectIds: [appealCase.caseId],
            jurisdiction: input.jurisdiction,
            beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
            payload: appealCase
        });
        return {
            ...text(`Appeal ${appealCase.caseId} filed.`),
            structuredContent: { appealCase, archiveReceipt: event.archiveEventId }
        };
    });
    return server;
}
// Stateless mode: no session id is issued or validated, which is what the
// per-request transport/server instances in index.ts require.
export function createHttpTransport() {
    return new StreamableHTTPServerTransport();
}
export { JURISDICTION_ENUM };
