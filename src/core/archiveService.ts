import { createHash } from "node:crypto";import { makeId } from "../utils/ids.js";import { nowIso } from "../utils/time.js";import type { ArchiveEvent } from "../types/domain.js";
const archiveStore: ArchiveEvent[] = [];
export class ArchiveService {  record(input: Omit<ArchiveEvent, "archiveEventId" | "timestamp" | "payloadHash">) {    const payloadHash = createHash("sha256")      .update(JSON.stringify(input.payload))      .digest("hex");
    const event: ArchiveEvent = {      archiveEventId: makeId("arc"),      timestamp: nowIso(),      payloadHash,      ...input    };
    archiveStore.push(event);    return event;  }
  query(params: {    eventType?: string;    actorId?: string;    subjectId?: string;    jurisdiction?: string;    page?: number;    pageSize?: number;  }) {    const page = params.page ?? 1;    const pageSize = Math.min(params.pageSize ?? 25, 100);
    let results = archiveStore;
    if (params.eventType) results = results.filter(e => e.eventType === params.eventType);    if (params.actorId) results = results.filter(e => e.actorIds.includes(params.actorId));    if (params.subjectId) results = results.filter(e => e.subjectIds.includes(params.subjectId));    if (params.jurisdiction) results = results.filter(e => e.jurisdiction === params.jurisdiction);
    const total = results.length;    const start = (page - 1) * pageSize;    const items = results.slice(start, start + pageSize);
    return { page, pageSize, total, items };  }}