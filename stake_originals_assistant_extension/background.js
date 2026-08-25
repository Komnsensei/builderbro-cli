chrome.runtime.onInstalled.addListener(() => {
  console.log('Stake.com Originals Assistant installed.');
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "popupOpened") {
    console.log("Popup opened message received by background script.");
    sendResponse({ status: "Background acknowledges popup" });
    return true; // Indicates an asynchronous response
  } else if (request.type === "fetchHistory") {
    console.log("Request to fetch history received.");
    // Here we would typically send a message to the content script of the active tab
    // to navigate to the history page and start scraping.
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs[0]) {
        chrome.tabs.sendMessage(tabs[0].id, { type: "startHistoryFetch" }, (response) => {
          if (response && response.success) {
            console.log("History fetch command sent to content script.");
            sendResponse({ success: true });
          } else {
            console.error("Failed to send history fetch command to content script or content script failed:", response);
            sendResponse({ success: false, error: response ? response.error : "No response from content script" });
          }
        });
      } else {
        console.error("No active tab found to send history fetch command.");
        sendResponse({ success: false, error: "No active tab" });
      }
    });
    return true; // Indicates an asynchronous response
  }
});

// Placeholder for storing history data.
// In a real scenario, the content script would send chunks of data to the background script
// which would then store it.
async function storeBetHistory(gameId, betData) {
    let result = await chrome.storage.local.get(['betHistory']);
    let betHistory = result.betHistory || {};
    if (!betHistory[gameId]) {
        betHistory[gameId] = [];
    }
    betHistory[gameId].push(betData); // Add new bet
    // TODO: Implement logic to keep only last 70 days if this becomes a stream
    await chrome.storage.local.set({ betHistory });
    console.log(`Stored bet for ${gameId}. Total for game: ${betHistory[gameId].length}`);
    // Notify popup of update
    let count = 0;
    for (const id in betHistory) {
        count += betHistory[id].length;
    }
    chrome.runtime.sendMessage({ type: "historyUpdate", count });
}