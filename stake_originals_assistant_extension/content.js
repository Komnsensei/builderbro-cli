console.log('Stake.com Originals Assistant content script loaded.');

// This script runs on Stake.com Originals pages.

// Listen for messages from the background script
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "startHistoryFetch") {
    console.log("Content script received command to start history fetch.");
    // This is where the complex logic for navigating to "My Bets"
    // and scraping data will go.
    // For now, just a placeholder response.
    alert("BRO! The content script is supposed to start fetching history now. This is a placeholder alert. I need to navigate to the bets history page.");
    sendResponse({ success: true, message: "History fetch command received by content script." });
    return true; // Indicates an asynchronous response
  }
  // This content script will also be responsible for:
  // 1. Observing game rounds (DOM changes, network requests)
  // 2. Extracting live game data
  // 3. Injecting overlay UI
  // 4. Sending live game data to background script for processing/predictions
});

// Example of trying to detect game type
function detectOriginalGame() {
  const url = window.location.href;
  const match = url.match(/\/casino\/originals\/([a-zA-Z0-9_-]+)/);
  if (match && match[1]) {
    console.log(`Currently on Stake Originals game: ${match[1]}`);
    return match[1];
  }
  return null;
}

const currentGame = detectOriginalGame();
if (currentGame) {
  // If we are on an originals game page, we might want to inject an overlay here.
  // For now, let's just log.
  console.log(`Content script active on an Originals game page: ${currentGame}`);
}

// Placeholder for real-time game observation
function observeLiveGame() {
  // This will involve MutationObservers for DOM changes,
  // or intercepting network requests if Stake uses an API for game state.
  console.log("Placeholder for live game observation.");
}

// observeLiveGame(); // Uncomment when ready to implement live observation
