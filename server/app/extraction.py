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

import re
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urljoin

import trafilatura

# Markdown image links: ![alt](src)
_IMAGE_SRC = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)(\)|\s)")

# A leading '# Heading' line, which is usually the article's own <h1>.
_LEADING_H1 = re.compile(r"^\s*#\s+(.+?)\s*(?:\n|$)")

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


def extract(html: str, url: str, mode: str = "article") -> Extracted | None:
    """
    Turn raw page HTML into Markdown plus metadata.

    mode:
      article — trafilatura at default precision. The normal path.
      page    — trafilatura with favor_recall, keeps more marginal content.

    Returns None when nothing usable was found.
    """
    if not html or not html.strip():
        return None

    meta = _metadata(html, url)

    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=True,
        include_tables=True,
        include_formatting=True,
        include_comments=False,
        favor_recall=(mode == "page"),
    )

    if not markdown or not markdown.strip():
        return None

    markdown = _absolutise_images(markdown.strip(), url)
    # Raw conversion can leave long runs of blank lines.
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = _strip_leading_title(markdown, str(meta.get("title") or ""))

    if not markdown.strip():
        return None

    return Extracted(
        markdown=markdown,
        word_count=len(markdown.split()),
        strategy=f"trafilatura ({mode})",
        site_category=_site_category(html, list(meta.get("categories") or [])),
        **meta,  # type: ignore[arg-type]
    )
