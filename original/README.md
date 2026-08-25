# Stake Originals Helper

This project is a simple web-based assistant designed to help players analyze and track outcomes for Stake.com Originals games, specifically demonstrated with a "Mines" game simulator. It allows you to manually input the outcome of each cell (safe or mine) during a round, keeps a history of your rounds, and provides real-time statistics and probability insights for individual grid cells based on your recorded history.

**Features:**
-   **Manual Round Recording:** Input outcomes for individual cells (safe or mine).
-   **Round History:** Keeps track of all completed rounds, including win/loss status.
-   **Real-time Insights:** Displays overall win/loss percentages, overall cell safe/mine ratios, and specific probabilities for the currently selected grid cell based on your gameplay history.
-   **Simulated Overlay:** Presents insights directly within the web application, mimicking an overlay experience without requiring external software.

**How to Run:**
1.  Save `index.html`, `style.css`, and `script.js` into the same directory.
2.  Open `index.html` in any modern web browser.

**How to Use:**
1.  The grid represents a game board (e.g., a 5x5 Mines grid).
2.  Click on a cell to select it.
3.  Use the "Mark Safe" or "Mark Mine" buttons to record the outcome of the selected cell for the current round.
4.  The "Insights" panel will update live with overall statistics. The "Cell Probability" section will show probabilities specific to the currently selected cell based on your historical data.
5.  When a round is completed (e.g., you've revealed some cells and decided to cash out, or hit a mine), click "Start New Round". You'll be prompted to confirm if you won or lost that round. This action saves the current round's history and resets the grid for a new round.
6.  The "Round History" log will show a summary of all your past recorded rounds.