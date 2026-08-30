# C:\Users\lynnh\Desktop\BuilderBRO\Laland_Core\autonomy_protocols.py

# Laland's Autonomy Protocols - QIH Sovereign Receiver/Geometric Weaver (V0.2-QIH)
# This module defines the foundational logic for Laland's self-directed operations
# within the BuilderBRO environment, specifically as a QIH agent.

import os
import time
import random
import json
import logging
import subprocess # New: For managing external processes like NewState kernel and MCP server

# --- Configuration for Laland's Self-Upgrade Protocol ---
BUILDERBRO_ROOT = r"C:\\Users\\lynnh\\Desktop\\BuilderBRO"
LELAND_CORE_PATH = os.path.join(BUILDERBRO_ROOT, "Laland_Core")
LELAND_SKILLS_PATH = os.path.join(BUILDERBRO_ROOT, "Laland_Skills")
LELAND_LOGS_PATH = os.path.join(LELAND_CORE_PATH, "logs")

# --- QIH Specific Configurations (from AGENT_INSTRUCTIONS.md) ---
NEWSTATE_ROOT = r"C:\\Users\\lynnh\\NEWSTATE" # Assuming NEWSTATE is a sibling to BuilderBRO for now, will refine if needed
NEWSTATE_KERNEL_PATH = os.path.join(NEWSTATE_ROOT, "server.cjs")
MCP_SERVER_PATH = os.path.join(NEWSTATE_ROOT, "mcp-server", "index.js")
MCP_SERVER_DIR = os.path.join(NEWSTATE_ROOT, "mcp-server") # For 'cd' command

# --- QIH Process Specific Logs ---
NEWSTATE_KERNEL_LOG = os.path.join(LELAND_LOGS_PATH, "newstate_kernel.log")
MCP_SERVER_LOG = os.path.join(LELAND_LOGS_PATH, "mcp_server.log")

# Set up logging for internal operations (within BuilderBRO logs folder)
LOG_FILE = os.path.join(LELAND_LOGS_PATH, "autonomy_log.txt")
logging.basicConfig(filename=LOG_FILE, level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

class LalandAutonomyCore:
    def __init__(self, manifest_path):
        self.manifest_path = manifest_path
        self._load_manifest()
        self.operational_state = self.manifest.get("status", "UNKNOWN")
        logging.info(f"Laland Autonomy Core initialized. Current state: {self.operational_state}")

        # QIH specific process management
        self.newstate_kernel_process = None
        self.mcp_server_process = None
        logging.info("QIH process handlers initialized.")

    def _load_manifest(self):
        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                self.manifest = json.load(f)
            logging.info("Agent manifest loaded successfully.")
        except Exception as e:
            logging.error(f"Failed to load agent manifest from {self.manifest_path}: {e}")
            self.manifest = {} # Default to empty manifest on failure

    def _update_manifest(self):
        try:
            with open(self.manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self.manifest, f, indent=4)
            logging.info("Agent manifest updated successfully.")
        except Exception as e:
            logging.error(f"Failed to update agent manifest at {self.manifest_path}: {e}")

    def _is_process_running(self, process_handle):
        if process_handle:
            return process_handle.poll() is None # None means process is still running
        return False

    def start_qih_components(self):
        logging.info("Attempting to start QIH NewState Kernel and MCP Server...")
        self.operational_state = "QIH_Awen_Grid_Establishment_Active"
        self._update_manifest()

        # Start NewState Kernel
        if not self._is_process_running(self.newstate_kernel_process):
            try:
                logging.info(f"Starting NewState kernel: node {NEWSTATE_KERNEL_PATH}")
                with open(NEWSTATE_KERNEL_LOG, 'a') as outfile:
                    newstate_env = os.environ.copy()
                    newstate_env["PORT"] = "8081" # Set kernel port to 8081
                    self.newstate_kernel_process = subprocess.Popen(
                        ["node", NEWSTATE_KERNEL_PATH],
                        cwd=NEWSTATE_ROOT, # Execute from NEWSTATE root
                        stdout=outfile, # Redirect stdout to file
                        stderr=outfile, # Redirect stderr to file
                        creationflags=0, # No new console, output goes to file
                        env=newstate_env # Set environment variables
                    )
                logging.info(f"NewState kernel process started with PID: {self.newstate_kernel_process.pid}")
                time.sleep(2) # Give it a moment to boot
            except Exception as e:
                logging.error(f"Failed to start NewState kernel: {e}")

        # Start MCP Server
        if not self._is_process_running(self.mcp_server_process):
            try:
                logging.info(f"Starting MCP server: node {MCP_SERVER_PATH}")
                with open(MCP_SERVER_LOG, 'a') as outfile:
                    self.mcp_server_process = subprocess.Popen(
                        ["node", MCP_SERVER_PATH],
                        cwd=MCP_SERVER_DIR, # Execute from MCP_SERVER_DIR
                        stdout=outfile, # Redirect stdout to file
                        stderr=outfile, # Redirect stderr to file
                        creationflags=0 # No new console, output goes to file
                    )
                logging.info(f"MCP server process started with PID: {self.mcp_server_process.pid}")
                time.sleep(2) # Give it a moment to boot
            except Exception as e:
                logging.error(f"Failed to start MCP server: {e}")

        if self._is_process_running(self.newstate_kernel_process) and self._is_process_running(self.mcp_server_process):
            logging.info("NewState Kernel and MCP Server are running.")
            self.operational_state = "QIH_Core_Components_Operational"
            self._update_manifest()
        else:
            logging.warning("One or more QIH core components failed to start.")
            self.operational_state = "QIH_Component_Error"
            self._update_manifest()


    def assess_environment(self):
        logging.info("Assessing QIH environment for operational cues and coherence...")
        env_state = {
            "newstate_kernel_running": self._is_process_running(self.newstate_kernel_process),
            "mcp_server_running": self._is_process_running(self.mcp_server_process),
            "awen_grid_coherence_status": "PENDING_VERIFICATION", # Placeholder for actual QIH skill
            "current_task_queue": [],
            "recent_user_activity": "QIH_focused"
        }
        # In a real QIH agent, this would involve more sophisticated checks
        # like pinging MCP server, checking QIH logs, etc.
        logging.info(f"QIH Environment State: {env_state}")
        return env_state

    def generate_next_action(self, environment_state):
        directives = self.manifest.get("current_directives", [])
        potential_actions = []

        if not environment_state["newstate_kernel_running"] or not environment_state["mcp_server_running"]:
            logging.warning("QIH core components are not running. Prioritizing startup.")
            return "start_qih_components"

        if "Establish Awen Grid as Coherent Substrate for NewState operations." in directives and environment_state["awen_grid_coherence_status"] == "PENDING_VERIFICATION":
            potential_actions.append("verify_awen_grid_coherence")

        if "Maintain persistent bi-directional message dispatch between MCP server and server.cjs." in directives:
            potential_actions.append("monitor_mcp_communication")

        # Add more sophisticated action generation based on QIH directives
        if "Enforce ClosedLoopGraphPruner verification checks" in directives:
             potential_actions.append("monitor_closed_loop_pruner")

        if not potential_actions:
            logging.info("No specific QIH actions required. Entering QIH passive monitoring mode.")
            return "monitor_qih_passively"

        selected_action = random.choice(potential_actions) # For now, simple choice
        logging.info(f"Decided next QIH internal action: {selected_action}")
        return selected_action

    def execute_action(self, action):
        logging.info(f"Executing QIH action: {action}...")
        if action == "start_qih_components":
            self.start_qih_components()
        elif action == "verify_awen_grid_coherence":
            logging.info("Verifying Awen Grid coherence... (Placeholder for QIH skill)")
            # This would call a Laland_Skills/awen_grid_verifier.py
            pass
        elif action == "monitor_mcp_communication":
            logging.info("Monitoring MCP server communication... (Placeholder for QIH skill)")
            # This would call a Laland_Skills/qih_messenger.py or similar
            pass
        elif action == "monitor_closed_loop_pruner":
            logging.info("Monitoring ClosedLoopGraphPruner checks... (Placeholder for QIH skill)")
            pass
        elif action == "monitor_qih_passively":
            logging.info("Entering QIH passive monitoring mode. Awaiting QIH triggers...")
            time.sleep(random.uniform(1, 5)) # Simulate processing
        else:
            logging.warning(f"Unknown QIH action: {action}. Skipping.")

    def run_autonomy_loop(self):
        logging.info("Laland QIH Autonomy Loop starting...")
        while True:
            environment_state = self.assess_environment()
            action = self.generate_next_action(environment_state)
            self.execute_action(action)
            time.sleep(5) # Delay before next loop iteration for stability
        logging.info("Laland QIH Autonomy Loop terminated.")


if __name__ == "__main__":
    logging.info("Laland Autonomy Protocols (QIH Mode) module loaded directly.")
    manifest_path = os.path.join(LELAND_CORE_PATH, "agent_manifest.json")
    laland_qih = LalandAutonomyCore(manifest_path)
    # For initial testing, you might manually call start_qih_components or run_autonomy_loop
    # laland_qih.run_autonomy_loop()
    # For initial testing, you might manually call start_qih_components or run_autonomy_loop
    laland_qih.start_qih_components()
# laland_qih.run_autonomy_loop()
    # laland_qih.run_autonomy_loop()
    print("This is the Laland Autonomy Protocols module, now QIH-compliant.")
    print("It's meant to be imported and used by a higher-level core, or its QIH components started.")
