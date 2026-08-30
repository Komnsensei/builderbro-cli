import dotenv from "dotenv";dotenv.config();

export const env = {
  port: Number(process.env.PORT || 8080),
  nodeEnv: process.env.NODE_ENV || "development",
  defaultJurisdiction: process.env.DEFAULT_JURISDICTION || "GLOBAL",
  archiveLedgerMode: process.env.ARCHIVE_LEDGER_MODE || "mock"
};