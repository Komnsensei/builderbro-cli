import { makeId } from "../utils/ids.js";
export class EmbodiedSystemService {
    systems = new Map();
    register(input) {
        const systemId = input.systemId || makeId("sys");
        const newSystem = {
            systemId: systemId,
            ownerLicenseId: input.ownerLicenseId,
            deviceClass: input.deviceClass,
            jurisdiction: input.jurisdiction,
            registrationDate: new Date().toISOString(),
            status: "registered",
        };
        this.systems.set(systemId, newSystem);
        return newSystem;
    }
}
export const embodiedSystems = new EmbodiedSystemService();
