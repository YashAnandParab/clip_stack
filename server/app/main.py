"""
Clip server.

Receives finished Markdown from the browser extension, applies a template,
and writes a .md file into the mounted vault.

Deliberately does no scraping. The browser already has the page, already
logged in, already rendered. Fetching it again from here would land you back
in the anonymous-request problem this whole design exists to avoid.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from .assets import process_images
from .config import BUILTIN_TEMPLATE_DIR, TEMPLATE_DIR, VAULT_PATH, store
from .database import (
    database_available,
    delete_clip_record,
    get_clip,
    init_database,
    list_clips,
    record_clip,
)
from .extraction import extract as extract_content
from .naming import apply_conflict_policy, resolve_in_vault, slugify
from .ocr import OCR_BASE_URL, OCR_ENABLED

log = logging.getLogger("clipserver")
logging.basicConfig(level=os.environ.get("CLIP_LOG_LEVEL", "INFO"))

TOKEN = os.environ.get("CLIP_TOKEN", "").strip()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    yield

app = FastAPI(title="Clip server", version="1.0.0", docs_url="/docs", lifespan=lifespan)

# The extension calls this from a chrome-extension:// origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome|moz)-extension://.*$|^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Templating
# ---------------------------------------------------------------------------

def _yaml_escape(value: Any) -> str:
    """Quote and escape a value so it is safe on the right of a YAML key."""
    text = "" if value is None else str(value)
    if text == "":
        return '""'
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = re.sub(r"\s*\n\s*", " ", text)
    return f'"{text}"'


def _yaml_list(value: Any) -> str:
    """['a', 'b'] or 'a, b' -> '"a", "b"' for an inline YAML array."""
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    else:
        items = [str(v).strip() for v in (value or [])]
    return ", ".join(_yaml_escape(i) for i in items if i)


def _build_env() -> Environment:
    loaders = []
    if TEMPLATE_DIR.exists():
        loaders.append(FileSystemLoader(str(TEMPLATE_DIR)))
    loaders.append(FileSystemLoader(str(BUILTIN_TEMPLATE_DIR)))

    env = Environment(
        loader=ChoiceLoader(loaders),
        # Never HTML-escape. The output is Markdown written to a file, so
        # turning "&" into "&amp;" would be actively wrong — including in
        # filenames, which are rendered through from_string().
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
    )
    env.filters["yaml"] = _yaml_escape
    env.filters["yaml_list"] = _yaml_list
    env.filters["slug"] = slugify
    return env


env = _build_env()


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class Clip(BaseModel):
    """What the extension sends: raw page HTML plus context."""

    html: str
    url: str
    mode: Literal["article", "page"] = "article"
    tags: list[str] = Field(default_factory=list)

    # Optional overrides. Anything blank is filled from trafilatura's metadata.
    title: str = ""
    domain: str = ""
    category: str = ""
    subcategory: str = ""

    # Per-clip routing overrides from the popup.
    subfolder: str | None = None
    filename: str | None = None
    template: str | None = None

    model_config = {"populate_by_name": True}


class URLRequest(BaseModel):
    url: str
    category: str = ""
    subcategory: str = ""
    tags: list[str] = Field(default_factory=list)


class ClipResult(BaseModel):
    ok: bool = True
    path: str
    absolute_path: str
    bytes: int
    rule: str
    template: str
    strategy: str = ""
    # What the note was filed as. Reported back so the popup can show what it
    # worked out, since nobody typed it in.
    category: str = ""
    subcategory: str = ""
    assets_saved: int = 0
    assets_found: int = 0
    ocr_succeeded: int = 0
    ocr_attempted: int = 0
    overwritten: bool = False
    history_id: int


class ClipHistoryItem(BaseModel):
    id: int
    url: str
    title: str
    site: str
    author: str
    published: str
    word_count: int
    strategy: str
    category: str
    subcategory: str
    path: str
    source_client: str
    clipped_at: datetime


class ClipHistoryResult(BaseModel):
    items: list[ClipHistoryItem]
    total: int


class DeleteClipResult(BaseModel):
    ok: bool = True
    id: int
    path: str
    file_deleted: bool
    assets_deleted: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_token(supplied: str | None) -> None:
    if not TOKEN:
        return  # auth disabled; fine on a localhost-only bind
    if supplied != TOKEN:
        raise HTTPException(status_code=401, detail="Bad or missing X-Clip-Token")


async def _clip_from_url(payload: URLRequest) -> Clip:
    parsed = urlparse(payload.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Enter a valid http(s) article URL.")

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": "Clip-to-Vault/1.0"},
        ) as client:
            response = await client.get(payload.url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=422, detail=f"Could not fetch that URL: {exc}") from exc

    return Clip(
        html=response.text,
        url=str(response.url),
        category=payload.category,
        subcategory=payload.subcategory,
        tags=payload.tags,
    )


def _domain_for(clip: Clip) -> str:
    """The popup's domain if it sent one, otherwise derived from the URL."""
    domain = clip.domain or urlparse(clip.url).hostname or ""
    return domain.removeprefix("www.")


def _variables(clip: Clip, doc, rule_tags: list[str]) -> dict[str, Any]:
    now = datetime.now().astimezone()
    tags = list(dict.fromkeys([*rule_tags, *clip.tags]))  # dedupe, keep order

    domain = _domain_for(clip)

    return {
        "title": clip.title or doc.title or "Untitled",
        "content": doc.markdown,
        "url": clip.url,
        "domain": domain,
        "site": doc.site or domain,
        "author": doc.author,
        "description": doc.description,
        "published": doc.published,
        "image": doc.image,
        "favicon": "",
        "language": doc.language,
        "word_count": doc.word_count,
        "wordCount": doc.word_count,
        "tags": tags,
        # The page's own filing, for `subcategory` in config.yaml to pick up.
        # category/subcategory themselves are added by _resolve_taxonomy once
        # these are known, so a path template can still reference them.
        "site_category": doc.site_category,
        "mode": clip.mode,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "datetime": now.isoformat(timespec="seconds"),
        "timestamp": now.strftime("%Y-%m-%d-%H-%M-%S"),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _render_string(template_text: str, variables: dict[str, Any]) -> str:
    return env.from_string(template_text).render(**variables)


def _subfolder_template(clip: Clip, rule) -> str:
    """A subfolder typed into the popup's override box, else the rule's."""
    return clip.subfolder if clip.subfolder is not None else rule.subfolder


def _resolve_taxonomy(clip: Clip, rule, variables: dict[str, Any]) -> None:
    """
    Work out what the note is about and add it to `variables`, in place.

    Popup values override automatic detection. Blank values fall back to the
    matched site rule and the category the page publishes for itself.

    Runs before the path templates so that those can reference either one.
    """

    def render(template_text: str) -> str:
        try:
            return _render_string(template_text, variables).strip()
        except Exception:
            return ""

    variables["category"] = clip.category.strip() or render(rule.category)
    variables["subcategory"] = clip.subcategory.strip() or render(rule.subcategory)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    vault_ok = VAULT_PATH.is_dir() and os.access(VAULT_PATH, os.W_OK)
    database_ok = database_available()
    return {
        "ok": vault_ok and database_ok,
        "vault": str(VAULT_PATH),
        "vault_writable": vault_ok,
        "database_ok": database_ok,
        "ocr_enabled": OCR_ENABLED,
        "ocr_url": OCR_BASE_URL if OCR_ENABLED else "",
        "auth_required": bool(TOKEN),
        "site_rules": store.site_count,
        "config_error": store.error,
    }


@app.post("/clip", response_model=ClipResult)
async def clip(
    payload: Clip,
    x_clip_token: str | None = Header(default=None),
    x_clip_client: str | None = Header(default=None),
) -> ClipResult:
    _check_token(x_clip_token)

    if not VAULT_PATH.is_dir():
        raise HTTPException(
            status_code=500,
            detail=f"Vault {VAULT_PATH} is not mounted. Check the volume in docker-compose.yml.",
        )

    doc = extract_content(payload.html, payload.url, payload.mode)
    if doc is None:
        raise HTTPException(
            status_code=422,
            detail='No readable content found on this page. Try "Whole page" mode, '
                   'or select the text you want and use "Selection".',
        )

    rule = store.rule_for(_domain_for(payload), payload.url)
    variables = _variables(payload, doc, rule.tags)
    _resolve_taxonomy(payload, rule, variables)

    # Per-clip overrides from the popup beat the config file.
    subfolder_tpl = _subfolder_template(payload, rule)
    filename_tpl = payload.filename if payload.filename is not None else rule.filename
    template_name = payload.template or rule.template

    try:
        subfolder = _render_string(subfolder_tpl, variables)
        filename = _render_string(filename_tpl, variables)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Bad path template: {exc}") from exc

    try:
        target = resolve_in_vault(VAULT_PATH, subfolder, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if rule.on_conflict == "skip" and target.exists():
        raise HTTPException(status_code=409, detail=f"Already clipped: {target.name}")

    overwritten = rule.on_conflict == "overwrite" and target.exists()
    target = apply_conflict_policy(target, rule.on_conflict)
    target.parent.mkdir(parents=True, exist_ok=True)

    images = await process_images(
        doc.markdown,
        target,
        payload.url,
        download_assets=rule.download_assets,
        ocr_images=rule.ocr_images,
    )
    variables["content"] = images.markdown

    try:
        note = env.get_template(template_name).render(**variables)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Template '{template_name}' failed: {exc}"
        ) from exc

    target.write_text(note, encoding="utf-8")

    relative = target.relative_to(VAULT_PATH.resolve())
    relative_path = str(relative).replace(os.sep, "/")
    try:
        history_id = record_clip(
            {**variables, "strategy": doc.strategy},
            relative_path,
            (x_clip_client or "unknown")[:40],
        )
    except Exception as exc:
        log.exception("clip was written but history could not be recorded")
        raise HTTPException(
            status_code=503,
            detail=f"Clip was saved to {relative_path}, but PostgreSQL history failed.",
        ) from exc

    log.info(
        "clipped %s -> %s (%s, %s) [%s / %s]",
        _domain_for(payload) or payload.url,
        relative,
        rule.matched,
        doc.strategy,
        variables["category"] or "-",
        variables["subcategory"] or "-",
    )

    return ClipResult(
        path=relative_path,
        absolute_path=str(target),
        bytes=len(note.encode("utf-8")),
        rule=rule.matched,
        template=template_name,
        strategy=doc.strategy,
        category=variables["category"],
        subcategory=variables["subcategory"],
        assets_saved=images.assets_saved,
        assets_found=images.assets_found,
        ocr_succeeded=images.ocr_succeeded,
        ocr_attempted=images.ocr_attempted,
        overwritten=overwritten,
        history_id=history_id,
    )


@app.get("/clips", response_model=ClipHistoryResult)
def clip_history(
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_clip_token: str | None = Header(default=None),
) -> ClipHistoryResult:
    _check_token(x_clip_token)
    items, total = list_clips(limit, offset)
    return ClipHistoryResult(items=items, total=total)


def _stored_path_in_vault(relative_path: str) -> Path:
    """Resolve a database path while refusing absolute or escaping paths."""
    stored = Path(relative_path)
    if stored.is_absolute() or ".." in stored.parts:
        raise HTTPException(status_code=409, detail="Stored clip path is unsafe; nothing deleted.")

    vault_root = VAULT_PATH.resolve()
    candidate = vault_root / stored
    if candidate.is_symlink():
        raise HTTPException(status_code=409, detail="Stored clip path is a symlink; nothing deleted.")
    target = candidate.resolve()
    if vault_root not in target.parents or target.suffix.lower() != ".md":
        raise HTTPException(status_code=409, detail="Stored clip path is unsafe; nothing deleted.")
    return target


@app.delete("/clips/{clip_id}", response_model=DeleteClipResult)
def delete_clip(
    clip_id: int,
    x_clip_token: str | None = Header(default=None),
) -> DeleteClipResult:
    """Permanently remove one history record, its note, and note-owned assets."""
    _check_token(x_clip_token)
    record = get_clip(clip_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    target = _stored_path_in_vault(str(record["path"]))
    assets_candidate = target.parent / "assets" / target.stem
    if assets_candidate.is_symlink():
        raise HTTPException(status_code=409, detail="Stored asset path is a symlink; nothing deleted.")
    assets_dir = assets_candidate.resolve()
    vault_root = VAULT_PATH.resolve()
    if vault_root not in assets_dir.parents:
        raise HTTPException(status_code=409, detail="Stored asset path is unsafe; nothing deleted.")

    try:
        file_deleted = target.is_file()
        if file_deleted:
            target.unlink()

        assets_deleted = assets_dir.is_dir()
        if assets_deleted:
            shutil.rmtree(assets_dir)

        assets_parent = assets_dir.parent
        if assets_parent.is_dir():
            try:
                assets_parent.rmdir()
            except OSError:
                pass  # other notes still own asset directories here
    except OSError as exc:
        log.exception("failed to delete clip files for history id %s", clip_id)
        raise HTTPException(status_code=500, detail=f"Could not delete clip files: {exc}") from exc

    if not delete_clip_record(clip_id):
        raise HTTPException(status_code=404, detail="Clip history record was already deleted.")

    log.info("deleted clip history id %s -> %s", clip_id, record["path"])
    return DeleteClipResult(
        id=clip_id,
        path=str(record["path"]),
        file_deleted=file_deleted,
        assets_deleted=assets_deleted,
    )


class PreviewResult(BaseModel):
    ok: bool = True
    html: str
    url: str
    markdown: str
    title: str
    author: str
    published: str
    site: str
    word_count: int
    strategy: str
    rule: str
    subfolder: str
    filename: str
    category: str = ""
    subcategory: str = ""
    # What the page declared, before the rule had its say. Lets the popup say
    # "the site didn't tell us" rather than just showing an empty field.
    site_category: str = ""


@app.post("/preview", response_model=PreviewResult)
async def preview(payload: Clip, x_clip_token: str | None = Header(default=None)) -> PreviewResult:
    """Extract and report back without writing anything."""
    _check_token(x_clip_token)

    doc = extract_content(payload.html, payload.url, payload.mode)
    if doc is None:
        raise HTTPException(
            status_code=422,
            detail='No readable content found on this page. Try "Whole page" mode, '
                   'or select the text you want and use "Selection".',
        )

    rule = store.rule_for(_domain_for(payload), payload.url)
    variables = _variables(payload, doc, rule.tags)
    _resolve_taxonomy(payload, rule, variables)

    try:
        subfolder = _render_string(_subfolder_template(payload, rule), variables)
        filename = _render_string(payload.filename or rule.filename, variables)
    except Exception:
        subfolder, filename = rule.subfolder, rule.filename

    return PreviewResult(
        html=payload.html,
        url=payload.url,
        markdown=doc.markdown,
        title=doc.title,
        author=doc.author,
        published=doc.published,
        site=doc.site,
        word_count=doc.word_count,
        strategy=doc.strategy,
        rule=rule.matched,
        subfolder=subfolder,
        filename=filename,
        category=variables["category"],
        subcategory=variables["subcategory"],
        site_category=doc.site_category,
    )


@app.post("/preview-url", response_model=PreviewResult)
async def preview_url(payload: URLRequest, x_clip_token: str | None = Header(default=None)) -> PreviewResult:
    """Fetch a public URL, then use the normal preview pipeline."""
    _check_token(x_clip_token)
    return await preview(await _clip_from_url(payload), x_clip_token)


@app.post("/clip-url", response_model=ClipResult)
async def clip_url(
    payload: URLRequest,
    x_clip_token: str | None = Header(default=None),
    x_clip_client: str | None = Header(default=None),
) -> ClipResult:
    """Fetch a public URL, then use the normal clip pipeline."""
    _check_token(x_clip_token)
    return await clip(await _clip_from_url(payload), x_clip_token, x_clip_client or "web")
