const GRID_SIZE = 5; // For a 5x5 Mines grid
let currentRoundOutcomes = {}; // Stores outcomes for the current round: {'x-y': 'safe' | 'mine'}
let roundHistory = []; // Stores completed round data: [{ outcomes: {}, isWin: true/false }]
let selectedCell = null; // Stores {x, y} of the currently selected cell

// DOM Elements
const gameGrid = document.getElementById('gameGrid');
const markSafeBtn = document.getElementById('markSafeBtn');
const markMineBtn = document.getElementById('markMineBtn');
const startNewRoundBtn = document.getElementById('startNewRoundBtn');

const totalRoundsSpan = document.getElementById('totalRounds');
const totalWinsSpan = document.getElementById('totalWins');
const winPercentageSpan = document.getElementById('winPercentage');
const totalLossesSpan = document.getElementById('totalLosses');
const lossPercentageSpan = document.getElementById('lossPercentage');
const overallSafeRatioSpan = document.getElementById('overallSafeRatio');
const overallMineRatioSpan = document.getElementById('overallMineRatio');

const selectedCellCoordsSpan = document.getElementById('selectedCellCoords');
const cellSafeCountSpan = document.getElementById('cellSafeCount');
const cellMineCountSpan = document.getElementById('cellMineCount');
const cellProbSafeSpan = document.getElementById('cellProbSafe');
const cellProbMineSpan = document.getElementById('cellProbMine');

const historyList = document.getElementById('historyList');

// --- Initialization ---
function initGrid() {
    gameGrid.innerHTML = ''; // Clear existing grid
    gameGrid.style.gridTemplateColumns = `repeat(${GRID_SIZE}, 1fr)`;
    gameGrid.style.gridTemplateRows = `repeat(${GRID_SIZE}, 1fr)`;

    for (let y = 0; y < GRID_SIZE; y++) {
        for (let x = 0; x < GRID_SIZE; x++) {
            const cell = document.createElement('div');
            cell.classList.add('grid-cell');
            cell.dataset.x = x;
            cell.dataset.y = y;
            cell.id = `cell-${x}-${y}`;
            cell.addEventListener('click', () => selectCell(x, y));
            gameGrid.appendChild(cell);
        }
    }
    updateGridDisplay();
    updateStats();
}

// --- Grid Interaction ---
function selectCell(x, y) {
    // Deselect previous cell
    if (selectedCell) {
        const prevCellEl = document.getElementById(`cell-${selectedCell.x}-${selectedCell.y}`);
        if (prevCellEl) prevCellEl.classList.remove('selected');
    }

    // Select new cell
    selectedCell = { x, y };
    const currentCellEl = document.getElementById(`cell-${x}-${y}`);
    currentCellEl.classList.add('selected');

    selectedCellCoordsSpan.textContent = `${x}, ${y}`;
    updateCellProbabilities();
}

function updateGridDisplay() {
    for (let y = 0; y < GRID_SIZE; y++) {
        for (let x = 0; x < GRID_SIZE; x++) {
            const cellId = `${x}-${y}`;
            const cellEl = document.getElementById(`cell-${x}-${y}`);
            cellEl.classList.remove('safe', 'mine'); // Clear previous outcome classes
            cellEl.textContent = ''; // Clear content

            if (currentRoundOutcomes[cellId] === 'safe') {
                cellEl.classList.add('safe');
                cellEl.textContent = '✔️';
            } else if (currentRoundOutcomes[cellId] === 'mine') {
                cellEl.classList.add('mine');
                cellEl.textContent = '💣';
            }
        }
    }
}

function markOutcome(outcome) {
    if (!selectedCell) {
        alert('Please select a cell first!');
        return;
    }
    const cellId = `${selectedCell.x}-${selectedCell.y}`;
    currentRoundOutcomes[cellId] = outcome;
    updateGridDisplay();
    updateCellProbabilities(); // Update immediately for the marked cell
}

// --- Round Management ---
function startNewRound(isWin = true) {
    if (Object.keys(currentRoundOutcomes).length > 0) {
        roundHistory.push({
            outcomes: { ...currentRoundOutcomes }, // Deep copy
            isWin: isWin
        });
    }

    currentRoundOutcomes = {}; // Reset for new round
    selectedCell = null; // Deselect cell
    selectedCellCoordsSpan.textContent = 'N/A';
    
    // Clear selection visually
    const allCells = document.querySelectorAll('.grid-cell');
    allCells.forEach(cell => cell.classList.remove('selected', 'safe', 'mine'));
    
    updateStats();
    updateHistoryLog();
    updateCellProbabilities(); // Clear probabilities for next round
}

// --- Stats and Insights ---
function updateStats() {
    const totalRounds = roundHistory.length;
    const totalWins = roundHistory.filter(round => round.isWin).length;
    const totalLosses = totalRounds - totalWins;

    totalRoundsSpan.textContent = totalRounds;
    totalWinsSpan.textContent = totalWins;
    winPercentageSpan.textContent = totalRounds > 0 ? ((totalWins / totalRounds) * 100).toFixed(2) : '0.00';
    totalLossesSpan.textContent = totalLosses;
    lossPercentageSpan.textContent = totalRounds > 0 ? ((totalLosses / totalRounds) * 100).toFixed(2) : '0.00';

    let totalRevealedCells = 0;
    let totalSafeCells = 0;
    let totalMineCells = 0;

    roundHistory.forEach(round => {
        Object.values(round.outcomes).forEach(outcome => {
            totalRevealedCells++;
            if (outcome === 'safe') {
                totalSafeCells++;
            } else {
                totalMineCells++;
            }
        });
    });

    // Also include current round in overall count for live tracking
    Object.values(currentRoundOutcomes).forEach(outcome => {
        totalRevealedCells++;
        if (outcome === 'safe') {
            totalSafeCells++;
        } else {
            totalMineCells++;
        }
    });

    overallSafeRatioSpan.textContent = totalRevealedCells > 0 ? ((totalSafeCells / totalRevealedCells) * 100).toFixed(2) : '0.00';
    overallMineRatioSpan.textContent = totalRevealedCells > 0 ? ((totalMineCells / totalRevealedCells) * 100).toFixed(2) : '0.00';
    
    updateCellProbabilities(); // Ensure this is called to update selected cell stats
}

function updateCellProbabilities() {
    if (!selectedCell) {
        cellSafeCountSpan.textContent = '0';
        cellMineCountSpan.textContent = '0';
        cellProbSafeSpan.textContent = '0.00';
        cellProbMineSpan.textContent = '0.00';
        return;
    }

    const cellId = `${selectedCell.x}-${selectedCell.y}`;
    let safeCount = 0;
    let mineCount = 0;

    // Check historical data
    roundHistory.forEach(round => {
        if (round.outcomes[cellId] === 'safe') {
            safeCount++;
        } else if (round.outcomes[cellId] === 'mine') {
            mineCount++;
        }
    });

    // Check current round data for live update on marked cell
    if (currentRoundOutcomes[cellId] === 'safe') {
        safeCount++;
    } else if (currentRoundOutcomes[cellId] === 'mine') {
        mineCount++;
    }

    const totalObservations = safeCount + mineCount;
    
    cellSafeCountSpan.textContent = safeCount;
    cellMineCountSpan.textContent = mineCount;
    cellProbSafeSpan.textContent = totalObservations > 0 ? ((safeCount / totalObservations) * 100).toFixed(2) : '0.00';
    cellProbMineSpan.textContent = totalObservations > 0 ? ((mineCount / totalObservations) * 100).toFixed(2) : '0.00';
}

function updateHistoryLog() {
    historyList.innerHTML = ''; // Clear previous history
    roundHistory.slice().reverse().forEach((round, index) => { // Display most recent first
        const listItem = document.createElement('li');
        const originalIndex = roundHistory.length - 1 - index;
        
        let outcomesSummary = Object.entries(round.outcomes)
                                    .map(([cellId, outcome]) => {
                                        const [x, y] = cellId.split('-');
                                        return `${outcome === 'safe' ? 'S' : 'M'}(${x},${y})`;
                                    })
                                    .join(', ');

        listItem.textContent = `Round ${originalIndex + 1}: ${round.isWin ? 'WIN' : 'LOSS'} - ${outcomesSummary}`;
        listItem.classList.add(round.isWin ? 'win' : 'loss');
        historyList.appendChild(listItem);
    });
}

// --- Event Listeners ---
markSafeBtn.addEventListener('click', () => markOutcome('safe'));
markMineBtn.addEventListener('click', () => markOutcome('mine'));
startNewRoundBtn.addEventListener('click', () => {
    // Prompt user for win/loss for the just-completed round
    const isWin = confirm('Did you win this round (cash out successfully)? Click OK for Win, Cancel for Loss.');
    startNewRound(isWin);
});

// Initial setup
initGrid();