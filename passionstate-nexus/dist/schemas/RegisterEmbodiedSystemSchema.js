import { z } from "zod";
export const RegisterEmbodiedSystemSchema = z.object({
    systemId: z.string().min(1).describe("Unique identifier for the embodied system."),
    ownerLicenseId: z.string().min(1).describe("The license ID of the system's owner."),
    deviceClass: z.string().min(1).describe("The classification of the embodied system (e.g., drone, autonomous vehicle, robot)."),
    jurisdiction: z.string().min(1).describe("The governing jurisdiction for the registration.")
});
