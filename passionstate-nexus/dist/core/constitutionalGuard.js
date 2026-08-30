import { ActionableError } from "../utils/errors.js";
import { GOVERNANCE_BEADS } from "../config/governanceBeads.js";
export function enforceConstitution(ctx) {
    const payloadText = JSON.stringify(ctx.payload ?? {}).toLowerCase();
    const coercionSignals = ["coerce", "manipulate", "exploit", "force without consent"];
    if (coercionSignals.some((term) => payloadText.includes(term))) {
        throw new ActionableError(`Action '${ctx.action}' violates constitutional vow '${GOVERNANCE_BEADS.NEVER_COERCE}'. Remove coercive intent and resubmit with compliant purpose.`, { action: ctx.action, jurisdiction: ctx.jurisdiction });
    }
    return {
        vowsApplied: [
            GOVERNANCE_BEADS.NEVER_COERCE,
            GOVERNANCE_BEADS.EXPAND_MEANING,
            GOVERNANCE_BEADS.ARCHIVE_EVERYTHING
        ]
    };
}
