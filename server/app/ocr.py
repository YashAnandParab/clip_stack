"""Thin HTTP client for the separately hosted MinerU OCR service."""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger("clipserver.ocr")

OCR_ENABLED = os.environ.get("OCR_ENABLED", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
OCR_BASE_URL = os.environ.get("OCR_BASE_URL", "http://mineru-ocr:8766").rstrip("/")
OCR_API_KEY = os.environ.get("OCR_API_KEY", "").strip()
OCR_TIMEOUT = float(os.environ.get("OCR_TIMEOUT", "300"))
OCR_MAX_IMAGES = max(0, int(os.environ.get("OCR_MAX_IMAGES", "10")))
OCR_MIN_IMAGE_BYTES = max(0, int(os.environ.get("OCR_MIN_IMAGE_BYTES", "4096")))
OCR_IMAGE_ANALYSIS = os.environ.get("OCR_IMAGE_ANALYSIS", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

SUPPORTED_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff", "image/gif",
}


def eligible_for_ocr(data: bytes, content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    return len(data) >= OCR_MIN_IMAGE_BYTES and media_type in SUPPORTED_TYPES


async def extract_image_markdown(
    client: httpx.AsyncClient,
    data: bytes,
    filename: str,
    content_type: str,
) -> str | None:
    """Return OCR Markdown, or None without failing the parent clip."""
    if not OCR_ENABLED or not eligible_for_ocr(data, content_type):
        return None

    headers = {"X-API-Key": OCR_API_KEY} if OCR_API_KEY else {}
    try:
        response = await client.post(
            f"{OCR_BASE_URL}/v1/ocr",
            headers=headers,
            files={"file": (filename, data, content_type)},
            data={
                "output_format": "json",
                "image_analysis": str(OCR_IMAGE_ANALYSIS).lower(),
            },
            timeout=OCR_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
        markdown = str(result.get("markdown") or result.get("text") or "").strip()
        markdown = markdown.replace("<!-- clipstack-ocr:start -->", "")
        markdown = markdown.replace("<!-- clipstack-ocr:end -->", "").strip()
        return markdown or None
    except Exception as exc:
        log.warning("OCR skipped for %s: %s", filename, exc)
        return None
