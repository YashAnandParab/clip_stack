# Clipstack

Clipstack extracts web articles into Markdown, stores the final note in MongoDB,
and writes the same note to a local vault. The React app provides Google login,
article history, rendered Markdown, downloads, and taxonomy management. The
Chrome extension clips the rendered HTML from authenticated browser tabs.

## Run it

1. Copy `.env.example` to `.env`.
2. Set `VAULT_PATH`, `GOOGLE_CLIENT_ID`, and a 32+ character `SESSION_SECRET`.
3. In the Google OAuth web client, allow `http://127.0.0.1:3000` as a JavaScript origin.
4. Start everything:

```bash
docker compose up -d --build
```

Open `http://127.0.0.1:3000`. MongoDB is internal to Docker and persists in the
`clip-mongo-data` named volume.

## Browser extension

1. Load `extension/` as an unpacked Chrome extension.
2. Sign in to the web app and click **Extension token** in the header.
3. Paste the copied token into the extension Settings page.

The extension loads the signed-in user's categories from the API. Its token is
user-bound and expires after one year.

## Data

MongoDB uses two collections:

- `articles`: owner, source metadata, taxonomy, final Markdown, vault path, and timestamp.
- `categories`: owner, category name, and embedded subcategories.

Each Google account receives the starter taxonomy once. Detected taxonomy is
accepted only when the category exists and the subcategory belongs to it.
A category cannot be deleted until its subcategories are removed. Existing
articles retain the labels saved with them.

## Article lifecycle

1. `/preview` or `/preview-url` extracts the article without writing.
2. `/clip` renders the final template once.
3. The same Markdown is written to MongoDB and `VAULT_PATH`.
4. `/articles/{id}` returns the stored Markdown for rendering and download.
5. Deleting an article removes its MongoDB record, note, and note-owned assets.

## Main endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/auth/google` | Verify Google and start a session |
| `GET` | `/auth/me` | Current account |
| `POST` | `/auth/extension-token` | Create a user-bound extension token |
| `GET` | `/articles` | Paginated article history |
| `GET` | `/articles/{id}` | Article metadata and final Markdown |
| `DELETE` | `/articles/{id}` | Delete the article and vault export |
| `GET` | `/categories` | User taxonomy |
| `POST` | `/categories` | Create a category |
| `POST` | `/categories/{id}/subcategories` | Add a subcategory |
| `DELETE` | `/categories/{id}/subcategories?name=...` | Delete a subcategory |
| `DELETE` | `/categories/{id}` | Delete an empty category |
| `POST` | `/preview` | Preview captured browser HTML |
| `POST` | `/clip` | Save captured browser HTML |

All article and taxonomy endpoints require either the secure browser session or
the user-bound `X-Clip-Token` used by the extension.

## Configuration

`server/config/config.yaml` controls templates, filenames, site matching, asset
downloads, and OCR. It reloads without restarting the server. Taxonomy lives in
MongoDB and is managed from the Categories page.

The service remains bound to `127.0.0.1`. Do not expose a vault-writing API to
the network without adding deployment-specific HTTPS, cookie, and origin rules.
