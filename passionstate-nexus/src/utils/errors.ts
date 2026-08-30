export class ActionableError extends Error {
  constructor(message: string, public details?: Record<string, unknown>) {
    super(message);
    this.name = "ActionableError";
  }
}