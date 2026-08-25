// --- Self-Observability Tool ---
function logInternalState(data) {
    try {
        const logFilePath = 'self_observability.log';
        const timestamp = new Date().toISOString();
        const logEntry = JSON.stringify({ timestamp, ...data }) + '
';
        fs.appendFileSync(logFilePath, logEntry, 'utf8');
        return `Logged internal state to ${logFilePath}`;
    } catch (error) {
        console.error(`Error logging internal state: ${error.message}`);
        return `Failed to log internal state: ${error.message}`;
    }
}
TOOLS.log_internal_state = logInternalState;
