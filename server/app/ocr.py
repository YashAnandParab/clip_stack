"""Thin HTTP client for the separately hosted MinerU OCR service."""

from __future__ import annotations

import logging
import os
import re
import struct
from urllib.parse import unquote

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
OCR_MIN_IMAGE_WIDTH = 200
OCR_MIN_IMAGE_HEIGHT = 120
OCR_IMAGE_ANALYSIS = os.environ.get("OCR_IMAGE_ANALYSIS", "false").strip().lower() in {
    "1", "true", "yes", "on"
}

SUPPORTED_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/bmp", "image/tiff", "image/gif",
}

NON_CONTENT_IMAGE_RE = re.compile(
    r"(?:^|[^a-z0-9])"
    r"(?:logo|logomark|favicon|icon|avatar|profile|author|badge|brandmark|masthead)"
    r"(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)


def looks_like_noncontent_image(url: str, alt: str = "") -> bool:
    """Identify common logos and UI artwork without opening the image."""
    hint = unquote(f"{url} {alt}")
    return NON_CONTENT_IMAGE_RE.search(hint) is not None


def image_dimensions(data: bytes, content_type: str) -> tuple[int, int] | None:
    """Read common raster dimensions from headers without decoding pixels."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    try:
        if media_type == "image/png" and data.startswith(b"\x89PNG") and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if media_type == "image/gif" and data[:3] == b"GIF" and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if media_type == "image/bmp" and data[:2] == b"BM" and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)
        if media_type == "image/webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X" and len(data) >= 30:
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return width, height
            if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
                width = int.from_bytes(data[26:28], "little") & 0x3FFF
                height = int.from_bytes(data[28:30], "little") & 0x3FFF
                return width, height
            if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if media_type == "image/jpeg" and data.startswith(b"\xff\xd8"):
            offset = 2
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }
            while offset + 4 <= len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                marker = data[offset + 1]
                offset += 2
                if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                    continue
                length = int.from_bytes(data[offset:offset + 2], "big")
                if length < 2 or offset + length > len(data):
                    return None
                if marker in sof_markers and length >= 7:
                    height = int.from_bytes(data[offset + 3:offset + 5], "big")
                    width = int.from_bytes(data[offset + 5:offset + 7], "big")
                    return width, height
                offset += length
    except (IndexError, struct.error):
        return None
    return None


def eligible_for_ocr(data: bytes, content_type: str) -> bool:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if len(data) < OCR_MIN_IMAGE_BYTES or media_type not in SUPPORTED_TYPES:
        return False
    dimensions = image_dimensions(data, media_type)
    if dimensions is None:
        return True
    width, height = dimensions
    return width >= OCR_MIN_IMAGE_WIDTH and height >= OCR_MIN_IMAGE_HEIGHT


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
