# Clipstack

Save what you're reading as Markdown, in a folder on your machine, with Python doing the work.

```
Chrome tab (already logged in)
        │  captures the rendered HTML — no parsing, no cleaning
        ▼
Extension  ──── POST http://127.0.0.1:8765/clip ────►  Python container
                                                              │
                                                  trafilatura extracts
                                                  route by domain
                                                  render Jinja template
                                                  download images
                                                  write the file
                                                              ▼
                                                    D:\notes\raw\...\note.md
```

The browser does the one thing only a browser can do — read a page you're authenticated to. Everything you'd actually want to change later lives in Python.

## Why not scrape it server-side

The distinction that matters is **fetching** versus **extracting**.

A scraping API gets a URL. A URL carries no cookies. It fetches the page anonymously, the site serves the anonymous version, and the paywalled text is never in the response at all — no parser can recover text that was never sent.

The container never fetches anything. It receives HTML the browser already built, with your session attached and JavaScript already run. A paid article you have access to clips in full.

Extraction — deciding which part of that HTML is the article — is a different job, and it belongs in Python where it's easy to change. That's [trafilatura](https://trafilatura.readthedocs.io): XPath heuristics with jusText and Readability as internal fallbacks. No per-site selectors are written here, ever. When a site redesigns, the library absorbs it.

---

## Setup

### 1. Start the server

```bash
cp .env.example .env
```

Edit `.env` — the only required line is where your notes go:

```bash
VAULT_PATH=/home/you/notes        # Linux/macOS
VAULT_PATH=D:/notes               # Windows, forward slashes
VAULT_PATH=/mnt/d/notes           # WSL2

CLIP_TOKEN=                       # optional, see below
PUID=1000                         # Linux only: `id -u`
PGID=1000                         # Linux only: `id -g`
```

Then:

```bash
docker compose up -d
curl http://127.0.0.1:8765/health
```

You should get `"ok": true` and `"vault_writable": true`.

Compose starts PostgreSQL, builds the server and web frontend, and keeps clip
history in a named database volume. Open
`http://127.0.0.1:3000` to clip a public article by URL. Set
`FRONTEND_PORT` in `.env` if you want to use a different frontend port.

The frontend loads saved clips from PostgreSQL when it opens and refreshes the
list every five seconds. Clips sent by the browser extension therefore appear
in the web UI without restarting it. The history and counter survive container
restarts and rebuilds; `docker compose down -v` is the command that deliberately
removes the database volume.

History is shown 10 articles at a time with Previous and Next controls. Open a
saved article and choose **Delete article** to permanently remove its PostgreSQL
record, Markdown file, and note-specific `assets/<note name>/` directory. The UI
asks for confirmation before sending the delete request.

`restart: unless-stopped` means it comes back on reboot. You start it once.

### 2. Load the extension

1. `chrome://extensions` → **Developer mode** on → **Load unpacked** → pick the `extension/` folder
2. The settings page opens; set the address to `http://127.0.0.1:8765`
3. Click **Test connection** — you want the green line
4. Pin the icon. **Ctrl+Shift+M** clips the current page.

The dot in the popup header is the server's status. Green means the container is up and the vault is writable.

---

## Modes

Three buttons in the popup. The mode is sent with the clip and decides how the server extracts.

| Mode | What the browser sends | What the server does |
|---|---|---|
| **Article** | the whole document | trafilatura at default precision — the normal path |
| **Whole page** | the whole document | trafilatura with `favor_recall`, keeps more marginal content |
| **Selection** | your selection, plus the real `<head>` | straight HTML→Markdown, no content detection |

Selection mode deliberately skips extraction. You already chose the content; running article detection over it would strip short selections. The `<head>` rides along so title, author and date still resolve.

Article and page mode send the **entire** document, not just `<body>` — `og:title`, `article:published_time` and JSON-LD all live in `<head>`.

The popup reports which path ran, and the response carries it as `strategy`.

---

## Rules

`server/config/config.yaml` decides how each clip is named, templated and tagged. First matching entry wins; edits apply on the next clip with no restart.

```yaml
defaults:
  subfolder: "inbox"
  filename: "{{ title }}"
  template: "default.md.j2"
  on_conflict: "uniquify"        # uniquify | overwrite | skip
  download_assets: false
  tags: ["clipped"]

sites:
  - match: ["medium.com", "*.medium.com"]
    template: "medium.md.j2"
    download_assets: true
    tags: ["article"]

  - match: ["*.youtube.com", "youtu.be"]
    filename: "{{ date }} {{ title | slug }}"
```

`match` is a glob against the domain or the full URL. `subfolder` and `filename` are Jinja templates, so `{{ year }}/{{ title | slug }}` works.

Note what the site rules no longer do: **set a folder**. Every clip lands in `defaults.subfolder`, which is `inbox`. Sorting is done by the category fields below, so a note is found by what it is about rather than by which directory it was filed into. A rule may still set `subfolder` if you want one site kept apart, but nothing ships that way.

## Categories

Every clip carries a **category** and a **subcategory**. Both are compulsory — `/clip` returns 422 without them, and **Send to vault** stays disabled until both are picked. They are written into the note's frontmatter, directly under `tags`:

```yaml
---
title: "How to Perform Effective Project Management with AI"
source: https://towardsdatascience.com/...
tags: ["article", "medium"]
category: "Work"
subcategory: "AI"
---
```

Ships with three categories, each holding two subcategories:

| Category | Subcategories |
|---|---|
| **Work** | AI · Database |
| **News** | Political · Sports |
| **Entertainment** | Movies · Songs |

The popup has one dropdown for each. Picking a category fills the second with its own subcategories; nothing is preselected.

### Adding your own

Both dropdowns end with **＋ New category…** / **＋ New subcategory…**, which open a one-field row right in the popup — no leaving the clip you were about to save. Settings has the full editor: categories with a Remove, subcategories as removable chips, and an inline box to add one.

The taxonomy lives in the extension, not in `config.yaml`, because the container mounts that file read-only and rewriting it from code would flatten the comments in it. Practically that means it is per-browser and does not survive reinstalling the extension.

Because these are only frontmatter, nothing has to be migrated when you rename or delete one — existing notes keep whatever they were clipped with.


## Templates

Drop any `.md.j2` file into `server/config/templates/` and name it in a rule. Files there shadow the built-ins.

```jinja
---
title: {{ title | yaml }}
source: {{ url }}
author: {{ author | yaml }}
tags: [{{ tags | yaml_list }}]
---

# {{ title }}

{{ content }}
```

**Variables:** `title` `content` `url` `domain` `site` `author` `description` `published` `image` `favicon` `language` `word_count` `tags` `category` `subcategory` `mode` `date` `time` `datetime` `timestamp` `year` `month` `day` `utc`

**Filters:** `| yaml` (quote and escape for frontmatter), `| yaml_list` (inline YAML array), `| slug`, plus all of Jinja's built-ins.

Always run `| yaml` on anything going into a frontmatter field. Titles contain colons and quotes, and unescaped they break the YAML block.

Built-ins: `default.md.j2`, `medium.md.j2` (callout header + source link), `link.md.j2` (bookmark, no body).

Two notes on the variables:

- `favicon` is always empty. Trafilatura doesn't report one. It stays in the list so existing templates don't break.
- The built-in templates render `# {{ title }}` above `{{ content }}`, and the extractor drops the article's own leading `<h1>` when it repeats the title — otherwise every note would carry its title twice. A heading that says something different is left alone.

## Images

Set `download_assets: true` on a rule. The server fetches each image, writes it to `assets/<note name>/` beside the note, and rewrites the links. A dead image is skipped, never fatal — the popup reports `3/4 images`.

Trafilatura resolves `<a href>` against the page URL but leaves `<img src>` relative, so image URLs are made absolute before anything else runs.

### OCR inside article images

Clipstack can send extracted article images to a separately hosted MinerU OCR
service and insert the returned Markdown immediately below the corresponding
image. No Playwright or BeautifulSoup is involved: the extension has already
rendered the page, and Trafilatura preserves useful images in the extracted
Markdown.

The MinerU container and `clip-server` must share the external `local-ai`
Docker network. The default endpoint is `http://mineru-ocr:8766/v1/ocr`.

```yaml
defaults:
  ocr_images: true

sites:
  - match: ["*.youtube.com", "github.com"]
    ocr_images: false
```

Useful `.env` controls are `OCR_ENABLED`, `OCR_BASE_URL`, `OCR_API_KEY`,
`OCR_TIMEOUT`, `OCR_MAX_IMAGES`, `OCR_MIN_IMAGE_BYTES`, and
`OCR_IMAGE_ANALYSIS`. OCR is submitted sequentially because MinerU serializes
GPU inference. Unsupported, tiny, inaccessible, or failed images are skipped;
the original clip still saves.

Injected text is bounded by `<!-- clipstack-ocr:start -->` and
`<!-- clipstack-ocr:end -->` comments. `download_assets` remains independent:
OCR can run while an image stays remote, or reuse the same downloaded bytes
when local asset saving is enabled.

---

## Endpoints

| | |
|---|---|
| `GET /health` | vault path, writability, auth, rule count, config errors |
| `GET /clips` | paginated persistent clip history (`limit` and `offset`) |
| `DELETE /clips/{id}` | permanently delete history, Markdown, and note-owned images |
| `POST /clip` | extract, route, render, write. Returns the path and `strategy` |
| `POST /preview` | extract and report back, **writing nothing** |
| `GET /docs` | FastAPI's interactive docs |

`/preview` exists because extraction moved server-side — the popup can no longer build the note itself, so it asks. Same payload as `/clip`. It's also what lets the popup show the real destination path instead of a placeholder.

Both take the same body: `html`, `url`, `mode`, `tags`, `category`, `subcategory`, and optional `title`, `subfolder`, `filename`, `template` overrides. `/clip` requires `category` and `subcategory`; `/preview` does not, because the popup previews before you have picked. A blank `title` means trafilatura's wins; the popup only sends one when you actually edit it.

---

## Security

The port binds to `127.0.0.1` only. This endpoint writes files to your disk; it has no business being reachable from your network. Don't change that line to `0.0.0.0`.

Set `CLIP_TOKEN` for a second layer:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put it in `.env` and in the extension's settings. Blank disables auth, which is defensible on a localhost-only bind.

Path traversal is handled server-side: `..` is stripped from templates and the resolved path is checked against the vault root before any write. A malicious template can't escape the mount.

---

## When the container is down

The extension falls back to `chrome.downloads` and saves **raw HTML** to `Downloads/Clips/`. There is no extractor in the browser any more, so it saves what it has rather than lose the clip. Nothing is thrown away — the file can be fed back through `/clip` once the container is up.

You'll also get no preview in the popup, for the same reason. It says so, and Save still works.

Turn the fallback off in settings if you'd rather see a hard failure.

---

## Layout

```
docker-compose.yml
.env.example
frontend/
  Dockerfile              builds the Vite app and serves it with Nginx
  nginx.conf              production static-file configuration
  src/                    React web interface
  package.json            frontend build scripts and dependencies
server/
  Dockerfile              python:3.12-slim + gosu
  entrypoint.sh           drops root to PUID/PGID so files aren't root-owned
  requirements.txt
  config/
    config.yaml           your routing rules  (mounted read-only)
    templates/            your custom templates
  app/
    main.py               /clip, /preview and /health
    extraction.py         trafilatura — the only extraction logic in the project
    config.py             YAML loading, glob matching, mtime reload
    naming.py             filename sanitising, traversal safety, conflicts
    assets.py             image download and link rewriting
    templates/            built-in templates
extension/
  manifest.json
  background.js           context menu, first-run
  content/extract.js      captures the tab's HTML. No parsing — that's the point
  lib/client.js           settings, HTTP, download fallback
  popup/  options/
```

Python is plain modules and the extension is plain ES modules with no bundle.
The optional React frontend is built automatically by Docker Compose.
PostgreSQL stores clip metadata in the `clip-postgres-data` named volume; the
Markdown article itself remains in `VAULT_PATH`.

`server/config/` is mounted, so routing rules and custom templates apply immediately. `server/app/` is **copied into the image**, so Python edits need `docker compose up -d --build` — a restart won't pick them up.

---

## What this gives up

Extraction is one benchmarked library rather than per-site cleverness. That's the trade, and it costs something:

- **YouTube transcripts don't work.** Fetching the transcript track is a site-specific job trafilatura doesn't do. The YouTube rule still names the file by date; you just get the page, not the transcript.
- **Reddit and Hacker News threads degrade.** Trafilatura scores far lower on forum-shaped pages than on articles.
- **Code blocks and maths lose fidelity.** No MathJax/KaTeX normalising, no stripping of line numbers from highlighted code.

For clipping articles, which is the actual use case, this extracts more reliably and needs no maintenance when a site redesigns.

---

## Troubleshooting

**`vault_writable: false`** — the mount didn't land, or PUID/PGID don't match you. Check with `docker compose exec clip-server ls -la /vault` and `id -u` on the host.

**Notes owned by root (Linux)** — set `PUID`/`PGID` in `.env` and `docker compose up -d --force-recreate`.

**Extension says "Not reachable"** — `docker compose ps` first. If it's up, confirm the port in the extension matches `CLIP_PORT`.

**401** — token mismatch between `.env` and the extension. They must be identical, or both empty.

**"No readable content found"** — a 422. Trafilatura found nothing it considered article text, which happens on very short pages and on app-shaped pages that aren't documents. Try **Whole page**, or select what you want and use **Selection**.

**A Python change didn't take effect** — `docker compose up -d --build`. `restart` reuses the old image.

**Windows paths** — use forward slashes in `.env`. `D:/notes`, not `D:\notes`.

---

## Where to take it

Everything below is a Python change, which is the point of the split:

- **Dedupe by URL** — keep an index of clipped URLs, return 409 on a repeat.
- **Post-process with an LLM** — summarise, tag, extract entities before writing. It's one more step in `main.py`.
- **A compile step** — a second endpoint that walks the vault and rebuilds an index from everything clipped so far.
- **Full-text search** — SQLite FTS5 over the vault, exposed as `/search`.
- **Re-process the fallbacks** — point an endpoint at `Downloads/Clips/*.html` and run them through the same pipeline.
