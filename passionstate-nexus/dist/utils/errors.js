export class ActionableError extends Error {
    details;
    constructor(message, details) {
        super(message);
        this.details = details;
        this.name = "ActionableError";
    }
}
