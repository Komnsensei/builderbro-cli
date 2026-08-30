import { makeId } from "../utils/ids.js";
const endorsementStore = new Map();
export class EndorsementService {
    apply(input) {
        const application = { endorsementApplicationId: makeId("end"), humanId: input.humanId, endorsementType: input.endorsementType, jurisdiction: input.jurisdiction, trainingRecords: input.trainingRecords, simulationResults: input.simulationResults, status: "under-review" };
        endorsementStore.set(application.endorsementApplicationId, application);
        return application;
    }
}
