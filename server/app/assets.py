"""Download article images, optionally save them and inject MinerU OCR."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from .ocr import OCR_ENABLED, OCR_MAX_IMAGES, OCR_TIMEOUT, eligible_for_ocr, extract_image_markdown

IMAGE_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)(?P<rest>\s+\"[^\"]*\")?\)")

MAX_BYTES = 12 * 1024 * 1024
DOWNLOAD_TIMEOUT = 20.0
CONCURRENCY = 5

EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


@dataclass
class FetchedImage:
    url: str
    data: bytes | None = None
    content_type: str = ""
    filename: str = ""


@dataclass
class ImageProcessingResult:
    markdown: str
    assets_saved: int = 0
    assets_found: int = 0
    ocr_succeeded: int = 0
    ocr_attempted: int = 0


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
    referer: str,
    sem: asyncio.Semaphore,
) -> FetchedImage:
    async with sem:
        try:
            headers = {"Referer": referer} if referer else {}
            response = await client.get(
                url,
                headers=headers,
                follow_redirects=True,
                timeout=DOWNLOAD_TIMEOUT,
            )
            response.raise_for_status()
            data = response.content
            if not data or len(data) > MAX_BYTES:
                return FetchedImage(url=url)

            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/"):
                content_type = mimetypes.guess_type(url.split("?", 1)[0])[0] or ""
            filename = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
            filename += _extension_for(url, content_type)
            return FetchedImage(url, data, content_type, filename)
        except Exception:
            return FetchedImage(url=url)


async def process_images(
    markdown: str,
    note_path: Path,
    page_url: str,
    *,
    download_assets: bool,
    ocr_images: bool,
) -> ImageProcessingResult:
    """Process Markdown images once while preserving their original order."""
    urls = list(dict.fromkeys(
        match.group("url")
        for match in IMAGE_RE.finditer(markdown)
        if match.group("url").startswith(("http://", "https://"))
    ))
    if not urls or not (download_assets or (ocr_images and OCR_ENABLED)):
        return ImageProcessingResult(markdown=markdown)

    sem = asyncio.Semaphore(CONCURRENCY)
    timeout = httpx.Timeout(max(DOWNLOAD_TIMEOUT, OCR_TIMEOUT))
    async with httpx.AsyncClient(timeout=timeout) as client:
        fetched = await asyncio.gather(
            *(_fetch_one(client, url, page_url, sem) for url in urls)
        )

        local_names: dict[str, str] = {}
        if download_assets:
            assets_dir = note_path.parent / "assets" / note_path.stem
            for image in fetched:
                if image.data is None:
                    continue
                assets_dir.mkdir(parents=True, exist_ok=True)
                (assets_dir / image.filename).write_bytes(image.data)
                local_names[image.url] = image.filename

        ocr_markdown: dict[str, str] = {}
        ocr_attempted = 0
        if ocr_images and OCR_ENABLED:
            # MinerU serializes inference on one GPU, so submit in article
            # order rather than creating concurrent requests.
            for image in fetched:
                if ocr_attempted >= OCR_MAX_IMAGES:
                    break
                if image.data is None or not eligible_for_ocr(image.data, image.content_type):
                    continue
                ocr_attempted += 1
                extracted = await extract_image_markdown(
                    client, image.data, image.filename, image.content_type
                )
                if extracted:
                    ocr_markdown[image.url] = extracted

    rel_prefix = "assets/" + quote(note_path.stem)

    def replace(match: re.Match[str]) -> str:
        url = match.group("url")
        local = local_names.get(url)
        rest = match.group("rest") or ""
        image_url = f"{rel_prefix}/{local}" if local else url
        image_markdown = f"![{match.group('alt')}]({image_url}{rest})"
        ocr = ocr_markdown.get(url)
        if not ocr:
            return image_markdown
        return (
            f"{image_markdown}\n\n"
            "<!-- clipstack-ocr:start -->\n\n"
            f"{ocr}\n\n"
            "<!-- clipstack-ocr:end -->"
        )

    return ImageProcessingResult(
        markdown=IMAGE_RE.sub(replace, markdown),
        assets_saved=len(local_names),
        assets_found=len(urls) if download_assets else 0,
        ocr_succeeded=len(ocr_markdown),
        ocr_attempted=ocr_attempted,
    )


async def localize_images(
    markdown: str, note_path: Path, page_url: str
) -> tuple[str, int, int]:
    """Compatibility wrapper for callers that only want local assets."""
    result = await process_images(
        markdown,
        note_path,
        page_url,
        download_assets=True,
        ocr_images=False,
    )
    return result.markdown, result.assets_saved, result.assets_found
