import { makeId } from "../utils/ids.js";
import type { RegisterEmbodiedSystemInput } from "../schemas/RegisterEmbodiedSystemSchema.js";
import type { EmbodiedSystem } from "../types/domain.js";

export class EmbodiedSystemService {
  private systems: Map<string, EmbodiedSystem> = new Map();

  register(input: RegisterEmbodiedSystemInput): EmbodiedSystem {
    const systemId = input.systemId || makeId("sys");
    const newSystem: EmbodiedSystem = {
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