"""
Optional image saving.

The extension sends Markdown with remote image URLs. If a rule turns on
download_assets, we fetch each one, write it next to the note, and rewrite the
link so the note still renders in five years when the CDN is gone.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

import httpx

# ![alt](url) and ![alt](url "title")
IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?P<rest>\s+\"[^\"]*\")?\)")

MAX_BYTES = 12 * 1024 * 1024
TIMEOUT = 20.0
CONCURRENCY = 5

EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
}


def _extension_for(url: str, content_type: str | None) -> str:
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in EXT_BY_TYPE:
            return EXT_BY_TYPE[base]
        guessed = mimetypes.guess_extension(base)
        if guessed:
            return guessed
    suffix = Path(url.split("?")[0]).suffix.lower()
    return suffix if 1 < len(suffix) <= 5 else ".img"


async def _fetch_one(
    client: httpx.AsyncClient,
    url: str,
    assets_dir: Path,
    referer: str,
    sem: asyncio.Semaphore,
) -> tuple[str, str | None]:
    """Returns (original_url, local_filename or None on failure)."""
    async with sem:
        try:
            headers = {"Referer": referer} if referer else {}
            resp = await client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()

            data = resp.content
            if not data or len(data) > MAX_BYTES:
                return url, None

            name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            name += _extension_for(url, resp.headers.get("content-type"))

            assets_dir.mkdir(parents=True, exist_ok=True)
            (assets_dir / name).write_bytes(data)
            return url, name
        except Exception:
            # A dead image should never fail the whole clip.
            return url, None


async def localize_images(
    markdown: str, note_path: Path, page_url: str
) -> tuple[str, int, int]:
    """
    Download every remote image referenced in the Markdown.

    Returns (rewritten_markdown, saved_count, attempted_count).
    Images live in `assets/<note stem>/` beside the note.
    """
    urls = {
        m.group("url")
        for m in IMAGE_RE.finditer(markdown)
        if m.group("url").startswith(("http://", "https://"))
    }
    if not urls:
        return markdown, 0, 0

    assets_dir = note_path.parent / "assets" / note_path.stem
    # Spaces in the note name would break the Markdown link, so encode them.
    rel_prefix = "assets/" + quote(note_path.stem)
    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *(_fetch_one(client, u, assets_dir, page_url, sem) for u in urls)
        )

    mapping = {url: name for url, name in results if name}

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        local = mapping.get(url)
        if not local:
            return match.group(0)
        rest = match.group("rest") or ""
        return f"![{match.group('alt')}]({rel_prefix}/{local}{rest})"

    return IMAGE_RE.sub(replace, markdown), len(mapping), len(urls)
