"""
Content extraction.

One library, no per-site rules. Trafilatura combines XPath heuristics with
jusText and Readability as internal fallbacks, so when a site redesigns, the
library absorbs it rather than this codebase.

The browser sends fully rendered, post-login HTML, which removes the usual
weakness of server-side extraction: there is no fetch here and no session to
replicate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin

import trafilatura
from html_table_rescuer import TableParser
from lxml import etree, html as lxml_html

log = logging.getLogger(__name__)

# Markdown image links: ![alt](src)
_IMAGE_SRC = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\)|\s)")

# A leading '# Heading' line, which is usually the article's own <h1>.
_LEADING_H1 = re.compile(r"^\s*#\s+(.+?)\s*(?:\n|$)")

# Table markers use only letters and digits so Trafilatura cannot escape or
# reformat them. The text between each pair is replaced with Table2MD output.
_TABLE_MARKER_PREFIX = "CLIPSTACKTABLE"

# <meta property="article:section" content="Behavioral Finance">. The lookahead
# only requires the name to appear somewhere in the tag, so it does not matter
# whether the CMS writes `property` or `content` first.
_SECTION_META = re.compile(
    r'<meta\b(?=[^>]*article:section)[^>]*\bcontent\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# The same value in JSON-LD, written either as a string or as an array.
_SECTION_LD = re.compile(
    r'"articleSection"\s*:\s*(?:"([^"]+)"|\[\s*"([^"]+)")',
    re.IGNORECASE,
)


@dataclass
class Extracted:
    markdown: str
    title: str = ""
    author: str = ""
    description: str = ""
    published: str = ""
    site: str = ""
    image: str = ""
    language: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    word_count: int = 0
    strategy: str = ""
    # The category the page declares for itself. See _site_category.
    site_category: str = ""


def _absolutise_images(markdown: str, url: str) -> str:
    """Trafilatura resolves <a> against the page URL but leaves <img> relative."""
    if not url:
        return markdown
    return _IMAGE_SRC.sub(lambda m: m.group(1) + urljoin(url, m.group(2)) + m.group(3), markdown)


def _norm(text: str) -> str:
    """Loose comparison key: case, spacing and trailing punctuation ignored."""
    return re.sub(r"\s+", " ", text).strip().strip(".!?:;-–—").casefold()


def _clean_category(value: str) -> str:
    """One category name: entities decoded, whitespace collapsed."""
    return unescape(re.sub(r"\s+", " ", str(value or ""))).strip()


def _site_category(html: str, trafilatura_categories: list[str]) -> str:
    """
    The category the page publishes for itself.

    Most CMSs say so outright. WordPress SEO plugins emit `article:section` in
    the head and repeat it as `articleSection` in JSON-LD; trafilatura also
    collects whatever it finds into `metadata.categories`. Reading the site's own
    taxonomy is worth far more than guessing from the text, because it is what
    the author actually filed the post under.

    Trafilatura's `tags` are deliberately not consulted. On a theme with a
    tag-cloud widget they come back as the whole sidebar rather than the post's
    own tags, so they are noise.

    Returns "" when the page declares nothing, which is left blank rather than
    guessed at.
    """
    match = _SECTION_META.search(html) or _SECTION_LD.search(html)
    if match:
        found = _clean_category(next(g for g in match.groups() if g))
        if found:
            return found

    for value in trafilatura_categories:
        cleaned = _clean_category(value)
        if cleaned:
            return cleaned

    return ""


def _strip_leading_title(markdown: str, title: str) -> str:
    """
    Drop the article's own <h1> when it just repeats the title.

    Every template renders '# {{ title }}' above '{{ content }}', so leaving
    trafilatura's heading in place puts the title in the note twice.
    """
    if not title:
        return markdown
    match = _LEADING_H1.match(markdown)
    if match and _norm(match.group(1)) == _norm(title):
        return markdown[match.end():].lstrip("\n")
    return markdown


def _table_markdown(table_html: str, caption: str = "") -> str | None:
    """Convert one HTML table with Table2MD's span-aware grid parser."""
    try:
        parsed_tables = TableParser(table_html).parse()
        converted = [table.to_markdown().strip() for table in parsed_tables]
        markdown = "\n\n".join(table for table in converted if table)
        if markdown and caption:
            markdown = f"**{caption}**\n\n{markdown}"
        return markdown or None
    except Exception:
        log.warning("Table2MD could not convert an HTML table", exc_info=True)
        return None


def _prepare_tables(html: str, url: str) -> tuple[str, dict[str, str]]:
    """
    Surround convertible tables with stable markers before extraction.

    Keeping the original table between the markers means it still contributes
    to Trafilatura's article-selection heuristics. After extraction, that
    provisional output is replaced with Table2MD's normalized Markdown.
    """
    try:
        document = lxml_html.document_fromstring(html)
    except (etree.ParserError, ValueError):
        return html, {}

    replacements: dict[str, str] = {}
    # Nested tables are handled by the outer table's parser.
    tables = document.xpath(".//table[not(ancestor::table)]")
    for index, table in enumerate(tables):
        # Table2MD preserves links itself, so resolve relative hrefs before it
        # sees the isolated table fragment.
        for linked in table.xpath(".//*[@href]"):
            href = linked.get("href")
            if href:
                linked.set("href", urljoin(url, href))
        table_html = lxml_html.tostring(table, encoding="unicode")
        captions = table.xpath("./caption")
        caption = (
            re.sub(r"\s+", " ", captions[0].text_content()).strip()
            if captions
            else ""
        )
        markdown = _table_markdown(table_html, caption)
        parent = table.getparent()
        if not markdown or parent is None:
            continue

        token = f"{_TABLE_MARKER_PREFIX}{index}"
        start = etree.Element("p")
        start.text = f"{token}START"
        end = etree.Element("p")
        end.text = f"{token}END"
        position = parent.index(table)
        parent.insert(position, start)
        parent.insert(position + 2, end)
        replacements[token] = markdown

    if not replacements:
        return html, {}
    return lxml_html.tostring(document, encoding="unicode"), replacements


def _restore_tables(markdown: str, replacements: dict[str, str]) -> str:
    """Replace Trafilatura's provisional table text with Table2MD Markdown."""
    for token, table_markdown in replacements.items():
        start = f"{token}START"
        end = f"{token}END"
        pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
        markdown, replaced = pattern.subn(
            lambda _: f"\n\n{table_markdown}\n\n", markdown, count=1
        )
        if not replaced:
            # Markers can be absent when a layout table was outside the article
            # selected by Trafilatura. Never leak an unmatched internal marker.
            markdown = markdown.replace(start, "").replace(end, "")
    return markdown


def _metadata(html: str, url: str) -> dict[str, object]:
    """Title, author, date, sitename. Never raises — metadata is best-effort."""
    try:
        meta = trafilatura.extract_metadata(html, default_url=url)
    except Exception:
        return {}
    if meta is None:
        return {}

    return {
        "title": meta.title or "",
        "author": meta.author or "",
        "description": meta.description or "",
        "published": meta.date or "",
        "site": meta.sitename or meta.hostname or "",
        "image": meta.image or "",
        "language": getattr(meta, "language", "") or "",
        "categories": list(meta.categories or []),
        "tags": list(meta.tags or []),
    }


def extract(html: str, url: str) -> Extracted | None:
    """
    Turn raw page HTML into Markdown plus metadata.

    Returns None when nothing usable was found.
    """
    if not html or not html.strip():
        return None

    meta = _metadata(html, url)

    extraction_html, table_replacements = _prepare_tables(html, url)

    markdown = trafilatura.extract(
        extraction_html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
        include_formatting=True,
        include_comments=False,
    )

    if not markdown or not markdown.strip():
        return None

    markdown = _restore_tables(markdown.strip(), table_replacements)
    markdown = _absolutise_images(markdown, url)
    # Raw conversion can leave long runs of blank lines.
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = _strip_leading_title(markdown, str(meta.get("title") or ""))

    if not markdown.strip():
        return None

    return Extracted(
        markdown=markdown,
        word_count=len(markdown.split()),
        strategy="trafilatura (article)",
        site_category=_site_category(html, list(meta.get("categories") or [])),
        **meta,  # type: ignore[arg-type]
    )
