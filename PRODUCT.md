# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React + Vite (delegated to the implementation)

## Users

The primary user is someone collecting financial and other web articles into a
personal vault while browsing on a desktop.

## Product Purpose

The product lets a user paste an article URL, fetch its metadata and content,
review the result, choose missing taxonomy values, and save the article to the
existing local vault.

## Positioning

It reuses the local clip server's authenticated article extraction and vault
writing flow, so the web UI and Chrome extension produce the same clips.

## Operating Context

The user works in a desktop browser with the local Python clip server running.
They review a growing set of scraped articles as cards and open one card to
inspect its metadata table.

## Capabilities and Constraints

- Paste a URL and request a scrape from the existing server.
- Show scraped articles as centered cards.
- Tapping a card toggles its metadata detail open and closed.
- Auto-detect category and subcategory where the existing scraper can.
- Require the user to choose missing category/subcategory values before saving.
- Save the completed article to the existing vault endpoint.
- Preserve the existing server as the scraper and source of truth.

## Brand Commitments

The existing product name is Clip to Vault. The UI should feel focused,
trustworthy, and practical rather than promotional.

## Evidence on Hand

The existing extraction, preview, clip, taxonomy, and vault code lives under
`server/app/`. The Chrome extension under `extension/` is the incumbent client.
No real article feed or external image assets were supplied.

## Product Principles

- Make paste-to-save the shortest path.
- Show the system's work before asking the user to trust it.
- Never guess taxonomy when the source is ambiguous.
- Keep review reversible and inline.

## Accessibility & Inclusion

The surface must support keyboard operation, visible focus, readable contrast,
responsive layouts, and explicit loading, error, empty, and disabled states.
