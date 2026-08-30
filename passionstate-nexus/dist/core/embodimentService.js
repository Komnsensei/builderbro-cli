import { makeId } from "../utils/ids.js";
const embodiedStore = new Map();
export class EmbodimentService {
    register(input) { const record = { ...input, status: "registered" }; embodiedStore.set(record.systemId || makeId("emb"), record); return record; }
}
