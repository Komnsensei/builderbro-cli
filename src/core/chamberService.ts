import { makeId } from "../utils/ids.js";
import type { OpenChamber } from "../types/domain.js";

const chamberStore = new Map<string, OpenChamber>();

export class ChamberService {
  create(input: Omit<OpenChamber, "chamberId" | "status">): OpenChamber {
    const chamber: OpenChamber = {
      chamberId: makeId("chm"),
      status: "active",
      ...input
    };
    chamberStore.set(chamber.chamberId, chamber);
    return chamber;
  }

  get(chamberId: string) {
    return chamberStore.get(chamberId);
  }
}