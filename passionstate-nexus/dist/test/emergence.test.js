import { describe, it, expect } from "vitest";
import { EmergenceService } from "../core/emergenceService.js";
describe("emergence review", () => {
    it("returns sovereign candidacy at high composite score", () => {
        const emergence = new EmergenceService();
        const result = emergence.runEmergenceReview({
            entityId: "ai_nexus_001",
            somaticScore: 92,
            prunerStabilityScore: 91,
            governorMaturityScore: 94,
            truthLayerScore: 90
        });
        expect(result.recommendation).toBe("sovereign identity candidacy");
    });
    it("returns provisional continuation for mid-high score", () => {
        const emergence = new EmergenceService();
        const result = emergence.runEmergenceReview({
            entityId: "ai_mid_001",
            somaticScore: 78,
            prunerStabilityScore: 76,
            governorMaturityScore: 79,
            truthLayerScore: 77
        });
        expect(result.recommendation).toBe("provisional review continuation");
    });
});
