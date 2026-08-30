import { z } from "zod";
export const IssueQuantumPassRequestSchema = z.object({
    humanId: z.string().min(1).describe("Unique ID of the human entity for whom the pass is to be issued"),
    jurisdiction: z.string().min(1).describe("Geopolitical or regulatory jurisdiction for the pass"),
});
export const QuantumPassRecordSchema = z.object({
    quantumPassId: z.string().min(1).describe("Unique identifier for the issued quantum pass"),
    humanId: z.string().min(1).describe("ID of the human entity holding this pass"),
    jurisdiction: z.string().min(1).describe("Jurisdiction where the pass is valid"),
    tier: z.string().min(1).describe("Tier or level of the quantum pass (e.g., 'Alpha', 'Beta', 'Omega')"),
    expiration: z.string().datetime().describe("ISO 8601 timestamp indicating when the pass expires"),
});
export const GetQuantumPassSchema = z.object({
    quantumPassId: z.string().min(1).describe("The unique ID of the quantum pass to retrieve or validate"),
});
