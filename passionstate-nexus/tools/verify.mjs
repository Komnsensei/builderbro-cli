// Standalone verification for environments where vitest's native rollup
// binary cannot load (e.g. Termux/bionic). Mirrors src/test/*.ts assertions.
import assert from "node:assert/strict";
import { ArchiveService } from "../dist/core/archiveService.js";
import { EmergenceService } from "../dist/core/emergenceService.js";
import { LiabilityService } from "../dist/core/liabilityService.js";
import { LicensingService } from "../dist/core/licensingService.js";
import { QuantumPassService } from "../dist/core/quantumPassService.js";
import { enforceConstitution } from "../dist/core/constitutionalGuard.js";
import { createMcpServer } from "../dist/server.js";

let passed = 0;
function ok(name, fn) {
  try {
    fn();
    passed++;
    console.log(`  ok - ${name}`);
  } catch (err) {
    console.error(`  FAIL - ${name}`);
    throw err;
  }
}

console.log("archive service");
{
  const archive = new ArchiveService();
  ok("creates immutable-style event records with payload hash", () => {
    const event = archive.record({
      eventType: "test_event",
      actorIds: ["actor_1"],
      subjectIds: ["subject_1"],
      jurisdiction: "GLOBAL",
      beadRefs: ["vow.archive_everything.v1"],
      payload: { hello: "world" }
    });
    assert.match(event.archiveEventId, /^arc_/);
    assert.ok(event.payloadHash.length > 10);
  });
  ok("queries archived events by event type", () => {
    archive.record({
      eventType: "find_me",
      actorIds: ["actor_2"],
      subjectIds: ["subject_2"],
      jurisdiction: "GLOBAL",
      beadRefs: ["vow.archive_everything.v1"],
      payload: { ok: true }
    });
    assert.equal(archive.query({ eventType: "find_me" }).total, 1);
  });
}

console.log("emergence review");
{
  const emergence = new EmergenceService();
  ok("returns sovereign candidacy at high composite score", () => {
    const result = emergence.runEmergenceReview({
      entityId: "ai_nexus_001",
      somaticScore: 92,
      prunerStabilityScore: 91,
      governorMaturityScore: 94,
      truthLayerScore: 90
    });
    assert.equal(result.recommendation, "sovereign identity candidacy");
  });
  ok("returns provisional continuation for mid-high score", () => {
    const result = emergence.runEmergenceReview({
      entityId: "ai_mid_001",
      somaticScore: 78,
      prunerStabilityScore: 76,
      governorMaturityScore: 79,
      truthLayerScore: 77
    });
    assert.equal(result.recommendation, "provisional review continuation");
  });
  ok("records somatic coherence", () => {
    const record = emergence.recordSomaticCoherence({
      entityId: "e1",
      sessionId: "s1",
      signalProfile: { calm: 0.8 },
      mappingMethod: "test-method",
      confidence: 0.9
    });
    assert.match(record.somaticRecordId, /^som_/);
  });
}

console.log("liability simulation");
{
  const liability = new LiabilityService();
  ok("assigns operator liability for non-conscious agents", () => {
    const result = liability.simulate({
      scenarioType: "property-damage",
      humanOperatorId: "human_1",
      agentId: "agent_1",
      emergenceStatus: "non-conscious",
      faultFactors: []
    });
    assert.equal(result.probableLiableParty, "human_1");
  });
  ok("assigns agent liability for sovereign agents without operator fault", () => {
    const result = liability.simulate({
      scenarioType: "property-damage",
      humanOperatorId: "human_1",
      agentId: "agent_1",
      emergenceStatus: "sovereign",
      faultFactors: []
    });
    assert.equal(result.probableLiableParty, "agent_1");
  });
}

console.log("licensing + quantum pass + constitution");
{
  const licensing = new LicensingService();
  ok("learner application requires modules and age", () => {
    const app = licensing.applyLearner({
      applicantId: "h1",
      age: 19,
      jurisdiction: "GLOBAL",
      educationModuleSet: ["VOW-101", "PROMPT-101", "RISK-101", "ARCHIVE-101", "ETHICS-101"],
      requestedAccessScope: "general"
    });
    assert.equal(app.status, "eligible");
    assert.throws(() =>
      licensing.applyLearner({
        applicantId: "h2",
        age: 10,
        jurisdiction: "GLOBAL",
        educationModuleSet: ["VOW-101", "PROMPT-101", "RISK-101", "ARCHIVE-101", "ETHICS-101"],
        requestedAccessScope: "general"
      })
    );
  });
  ok("issues license", () => {
    const license = licensing.issueLicense({ humanId: "h1", jurisdiction: "GLOBAL", tier: "general" });
    assert.match(license.licenseId, /^lic_/);
  });
  const passes = new QuantumPassService();
  ok("issues and updates quantum pass", () => {
    const pass = passes.issue("h1", "GLOBAL");
    const updated = passes.update(pass.quantumPassId, { careScore: 80, somaticCoherenceScore: 0.9 });
    assert.equal(updated.careScore, 80);
    assert.equal(updated.somaticCoherenceScore, 0.9);
  });
  ok("constitutional guard blocks coercion", () => {
    assert.throws(() =>
      enforceConstitution({ action: "test", jurisdiction: "GLOBAL", payload: { purpose: "coerce users" } })
    );
    const good = enforceConstitution({ action: "test", jurisdiction: "GLOBAL", payload: { purpose: "help" } });
    assert.ok(good.vowsApplied.length === 3);
  });
}

console.log("mcp server");
ok("exposes the full tool surface", async () => {
  const server = createMcpServer();
  // Internal registry check: server exposes tools via its internal _registeredTools
  const tools = server._registeredTools
    ? Object.keys(server._registeredTools)
    : [];
  assert.ok(tools.includes("passionstate_issue_quantum_pass"), "issue quantum pass registered");
  assert.ok(tools.includes("passionstate_run_emergence_review"), "emergence review registered");
  assert.ok(tools.includes("passionstate_run_liability_simulation"), "liability simulation registered");
  assert.ok(tools.includes("passionstate_validate_insurance_coverage"), "insurance validation registered");
  assert.ok(tools.includes("passionstate_register_embodied_system"), "embodied system registered");
  assert.ok(tools.length >= 15, `expected >=15 tools, got ${tools.length}`);
});

console.log(`\n${passed} assertions passed`);
