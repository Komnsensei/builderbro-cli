import { createHash } from "node:crypto";
import { makeId } from "../utils/ids.js";
import { nowIso } from "../utils/time.js";
const archiveStore = [];
export class ArchiveService {
    record(input) {
        const payloadHash = createHash("sha256")
            .update(JSON.stringify(input.payload))
            .digest("hex");
        const event = {
            archiveEventId: makeId("arc"),
            timestamp: nowIso(),
            payloadHash,
            ...input
        };
        archiveStore.push(event);
        return event;
    }
    query(params) {
        const page = params.page ?? 1;
        const pageSize = Math.min(params.pageSize ?? 25, 100);
        let results = archiveStore;
        if (params.eventType !== undefined)
            results = results.filter(e => e.eventType === params.eventType);
        if (params.actorId !== undefined) {
            const actorId = params.actorId;
            results = results.filter(e => e.actorIds.includes(actorId));
        }
        if (params.subjectId !== undefined) {
            const subjectId = params.subjectId;
            results = results.filter(e => e.subjectIds.includes(subjectId));
        }
        if (params.jurisdiction !== undefined)
            results = results.filter(e => e.jurisdiction === params.jurisdiction);
        const total = results.length;
        const start = (page - 1) * pageSize;
        const items = results.slice(start, start + pageSize);
        return { page, pageSize, total, items };
    }
}
