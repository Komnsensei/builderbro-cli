document.addEventListener('DOMContentLoaded', () => {
  const statusElement = document.getElementById('status');
  const fetchHistoryButton = document.getElementById('fetchHistory');
  const historyCountElement = document.getElementById('historyCount');

  statusElement.textContent = 'Ready';

  // Example of sending a message to background script
  chrome.runtime.sendMessage({ type: "popupOpened" }, (response) => {
    console.log("Background response:", response);
  });

  fetchHistoryButton.addEventListener('click', () => {
    statusElement.textContent = 'Fetching history...';
    // Send a message to the background script to initiate history fetching
    chrome.runtime.sendMessage({ type: "fetchHistory" }, (response) => {
      if (response && response.success) {
        statusElement.textContent = 'History fetch initiated.';
      } else {
        statusElement.textContent = 'Failed to initiate history fetch.';
      }
    });
  });

  // Listen for messages from background/content scripts to update UI
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.type === "historyUpdate") {
      historyCountElement.textContent = `Total bets stored: ${request.count}`;
    }
  });

  // On load, try to get current history count
  chrome.storage.local.get(['betHistory'], (result) => {
    const history = result.betHistory || {};
    let count = 0;
    for (const gameId in history) {
        count += history[gameId].length;
    }
    historyCountElement.textContent = `Total bets stored: ${count}`;
  });
});