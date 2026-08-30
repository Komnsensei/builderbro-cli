import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { enforceConstitution } from "../core/constitutionalGuard.js";
import { EmbodiedSystemService } from "../core/embodiedSystemService.js";
import { ArchiveService } from "../core/archiveService.js";
import { GOVERNANCE_BEADS } from "../config/governanceBeads.js";

const RegisterInput = z.object({
  systemId: z.string().min(1),
  ownerLicenseId: z.string().min(1),
  deviceClass: z.string().min(1),
  jurisdiction: z.string().min(1)
});

export function registerEmbodiedSystemTool(
  server: McpServer,
  systems: EmbodiedSystemService,
  archive: ArchiveService
): void {
  server.registerTool(
    "passionstate_register_embodied_system",
    {
      title: "Register Embodied System",
      description:
        "Registers an embodied system (robot, drone, AI agent) with the Passion State, assigning a unique identifier and establishing its legal presence.",
      inputSchema: RegisterInput.shape,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true }
    },
    (input) => {
      enforceConstitution({
        action: "register_embodied_system",
        jurisdiction: input.jurisdiction,
        payload: input
      });
      const system = systems.register(input);
      const event = archive.record({
        eventType: "embodied_system_registered",
        actorIds: [input.ownerLicenseId],
        subjectIds: [system.systemId],
        jurisdiction: input.jurisdiction,
        beadRefs: [GOVERNANCE_BEADS.ARCHIVE_EVERYTHING],
        payload: system
      });
      return {
        content: [{ type: "text", text: `Embodied system ${system.systemId} registered.` }],
        structuredContent: { system, archiveReceipt: event.archiveEventId }
      };
    }
  );
}
