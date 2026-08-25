#!/bin/bash
set -x

# Navigate to the script's directory
cd "$(dirname "$0")"

OUTPUT_FILE="dycrag_agent_output.log"

echo "--- Setting up DyCRAG Environment ---"

echo "DEBUG: Checking python3 availability..."
which python3

echo "DEBUG: Current directory for run.sh: $(pwd)"

echo "--- Starting DyCRAG Agent Demonstration ---"
echo ""

echo "DEBUG: Attempting to run python3 main.py..."
# Run the main Python application, passing along any arguments from run.sh
# Redirect all output to a temporary file
python3 -u main.py "$@" > "$OUTPUT_FILE" 2>&1

# Now display the captured output
echo "--- Captured DyCRAG Agent Output ---"
cat "$OUTPUT_FILE"
echo "--- End Captured DyCRAG Agent Output ---"

echo ""
echo "--- DyCRAG Demonstration Complete ---"
echo "To clean up, you can manually remove the 'dycrag_system' directory and the log file '$OUTPUT_FILE'."
