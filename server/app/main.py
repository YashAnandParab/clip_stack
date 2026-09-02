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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined
from pydantic import BaseModel, Field

from .assets import localize_images
from .config import BUILTIN_TEMPLATE_DIR, TEMPLATE_DIR, VAULT_PATH, store
from .extraction import extract as extract_content
from .naming import apply_conflict_policy, resolve_in_vault, slugify

log = logging.getLogger("clipserver")
logging.basicConfig(level=os.environ.get("CLIP_LOG_LEVEL", "INFO"))

TOKEN = os.environ.get("CLIP_TOKEN", "").strip()

app = FastAPI(title="Clip server", version="1.0.0", docs_url="/docs")

# The extension calls this from a chrome-extension:// origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(chrome|moz)-extension://.*$",
    allow_methods=["GET", "POST", "OPTIONS"],
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

    # Per-clip routing overrides from the popup.
    subfolder: str | None = None
    filename: str | None = None
    template: str | None = None

    model_config = {"populate_by_name": True}


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
    overwritten: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_token(supplied: str | None) -> None:
    if not TOKEN:
        return  # auth disabled; fine on a localhost-only bind
    if supplied != TOKEN:
        raise HTTPException(status_code=401, detail="Bad or missing X-Clip-Token")


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


def _resolve_taxonomy(rule, variables: dict[str, Any]) -> None:
    """
    Work out what the note is about and add it to `variables`, in place.

    Nobody is asked. `category` describes the source and comes from the matched
    site rule; `subcategory` defaults to `{{ site_category }}`, the category the
    page publishes for itself. A site that declares nothing gets a blank field
    rather than a guess — blank is searchable, a wrong guess is not.

    Runs before the path templates so that those can reference either one.
    """

    def render(template_text: str) -> str:
        try:
            return _render_string(template_text, variables).strip()
        except Exception:
            return ""

    variables["category"] = render(rule.category)
    variables["subcategory"] = render(rule.subcategory)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    vault_ok = VAULT_PATH.is_dir() and os.access(VAULT_PATH, os.W_OK)
    return {
        "ok": vault_ok,
        "vault": str(VAULT_PATH),
        "vault_writable": vault_ok,
        "auth_required": bool(TOKEN),
        "site_rules": store.site_count,
        "config_error": store.error,
    }


@app.post("/clip", response_model=ClipResult)
async def clip(payload: Clip, x_clip_token: str | None = Header(default=None)) -> ClipResult:
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
    _resolve_taxonomy(rule, variables)

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

    saved = found = 0
    if rule.download_assets:
        variables["content"], saved, found = await localize_images(
            doc.markdown, target, payload.url
        )

    try:
        note = env.get_template(template_name).render(**variables)
    except Exception as exc:
        raise HTTPException(
            status_code=422, detail=f"Template '{template_name}' failed: {exc}"
        ) from exc

    target.write_text(note, encoding="utf-8")

    relative = target.relative_to(VAULT_PATH.resolve())
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
        path=str(relative).replace(os.sep, "/"),
        absolute_path=str(target),
        bytes=len(note.encode("utf-8")),
        rule=rule.matched,
        template=template_name,
        strategy=doc.strategy,
        category=variables["category"],
        subcategory=variables["subcategory"],
        assets_saved=saved,
        assets_found=found,
        overwritten=overwritten,
    )


class PreviewResult(BaseModel):
    ok: bool = True
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
    _resolve_taxonomy(rule, variables)

    try:
        subfolder = _render_string(_subfolder_template(payload, rule), variables)
        filename = _render_string(payload.filename or rule.filename, variables)
    except Exception:
        subfolder, filename = rule.subfolder, rule.filename

    return PreviewResult(
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
