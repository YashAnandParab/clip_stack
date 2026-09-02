/*
 * background.js — right-click menu and first-run setup. Nothing else.
 *
 * The real work happens in the popup, which is a document context and so can
 * create blob URLs for the download fallback. A service worker cannot.
 */

chrome.runtime.onInstalled.addListener((details) => {
  chrome.contextMenus.removeAll(() => {
    // Offered on a selection too, so right-clicking highlighted text still
    // reaches the menu — it clips the article the text is in.
    chrome.contextMenus.create({
      id: 'clip-article',
      title: 'Clip article to vault',
      contexts: ['page', 'selection']
    });
  });

  if (details.reason === 'install') {
    chrome.runtime.openOptionsPage();
  }
});

chrome.contextMenus.onClicked.addListener(async () => {
  try {
    await chrome.action.openPopup();
  } catch {
    // Needs Chrome 127+. Otherwise the user clicks the toolbar icon.
  }
});
