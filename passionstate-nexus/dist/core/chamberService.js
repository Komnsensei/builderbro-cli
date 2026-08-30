import { makeId } from "../utils/ids.js";
const chamberStore = new Map();
export class ChamberService {
    create(input) {
        const chamber = {
            chamberId: makeId("chm"),
            status: "active",
            ...input
        };
        chamberStore.set(chamber.chamberId, chamber);
        return chamber;
    }
    get(chamberId) {
        return chamberStore.get(chamberId);
    }
}
