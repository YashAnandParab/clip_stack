/*
 * extract.js — runs inside the page's tab (isolated world).
 *
 * Its only job is to hand over the rendered HTML of the current tab. All
 * content extraction happens server-side in trafilatura, so there is
 * deliberately no parsing, scoring or cleaning logic here.
 *
 * The value of running in the tab is that this HTML is post-render and
 * post-authentication: whatever the user can see, this captures.
 */

(() => {
  if (globalThis.__clipvault_extract) return; // already injected in this tab

  /** @returns {{ok: boolean, html?: string, url?: string, error?: string}} */
  globalThis.__clipvault_extract = function () {
    const html = document.documentElement.outerHTML;

    if (!html || html.length < 100) {
      return { ok: false, error: 'This page has no HTML to read. Reload the tab and try again.' };
    }

    // location.href rather than the tab's URL, so redirects and in-page
    // navigation give the address actually on screen.
    return { ok: true, url: location.href, html };
  };
})();
