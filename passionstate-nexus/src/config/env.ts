import dotenv from "dotenv";
dotenv.config();

export const env = {
  port: Number(process.env.PORT || 8080),
  nodeEnv: process.env.NODE_ENV || "development",
  defaultJurisdiction: process.env.DEFAULT_JURISDICTION || "GLOBAL",
  archiveLedgerMode: process.env.ARCHIVE_LEDGER_MODE || "mock",
  /**
   * Optional bearer token guarding /mcp. When unset the endpoint is open so
   * local development and stdio transports keep working; a networked
   * deployment MUST set this (e.g. NEXUS_MCP_TOKEN=<64 random hex>).
   */
  mcpToken: process.env.NEXUS_MCP_TOKEN || "",
};
