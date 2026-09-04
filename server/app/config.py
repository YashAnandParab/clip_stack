"""
Config loading.

One YAML file describes where clips go. `defaults` applies to everything;
`sites` overrides it per domain, first match wins. The file is re-read when its
mtime changes, so you can edit rules without restarting the container.
"""

from __future__ import annotations

import fnmatch
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(os.environ.get("CLIP_CONFIG", "/config/config.yaml"))
VAULT_PATH = Path(os.environ.get("CLIP_VAULT", "/vault"))
TEMPLATE_DIR = Path(os.environ.get("CLIP_TEMPLATES", "/config/templates"))
BUILTIN_TEMPLATE_DIR = Path(__file__).parent / "templates"

DEFAULT_CONFIG: dict[str, Any] = {
    "defaults": {
        "subfolder": "inbox",
        "filename": "{{ title }}",
        "template": "default.md.j2",
        "on_conflict": "uniquify",
        "download_assets": False,
        "ocr_images": True,
        "tags": [],
        # Frontmatter, not routing, and nobody is asked for either at clip time.
        # `category` describes the source, so it is set per-site below.
        # `subcategory` defaults to the category the page publishes for itself,
        # which on a WordPress site is the author's own filing. Both are Jinja,
        # like subfolder and filename, and both stay blank when nothing answers.
        "category": "",
        "subcategory": "{{ site_category }}",
    },
    "sites": [],
}


@dataclass
class Rule:
    """The resolved settings for one incoming clip."""

    subfolder: str
    filename: str
    template: str
    on_conflict: str
    download_assets: bool
    ocr_images: bool = True
    category: str = ""
    subcategory: str = ""
    tags: list[str] = field(default_factory=list)
    matched: str = "defaults"


class ConfigStore:
    """Loads config.yaml and hands out resolved rules. Reloads on mtime change."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = DEFAULT_CONFIG
        self._mtime: float | None = None
        self._error: str | None = None
        self.reload()

    # -- loading ----------------------------------------------------------

    def reload(self) -> None:
        with self._lock:
            try:
                if not self.path.exists():
                    self._data = DEFAULT_CONFIG
                    self._error = f"{self.path} not found; using built-in defaults"
                    return

                mtime = self.path.stat().st_mtime
                if self._mtime == mtime:
                    return

                raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ValueError("config.yaml must be a mapping at the top level")

                merged = {
                    "defaults": {**DEFAULT_CONFIG["defaults"], **(raw.get("defaults") or {})},
                    "sites": raw.get("sites") or [],
                }
                if not isinstance(merged["sites"], list):
                    raise ValueError("`sites` must be a list")

                self._data = merged
                self._mtime = mtime
                self._error = None
            except Exception as exc:  # keep serving with the last good config
                self._error = f"{type(exc).__name__}: {exc}"

    # -- lookup -----------------------------------------------------------

    def rule_for(self, domain: str, url: str = "") -> Rule:
        """Resolve the rule for a domain. First matching site entry wins."""
        self.reload()
        with self._lock:
            defaults = self._data["defaults"]
            sites = self._data["sites"]

        merged = dict(defaults)
        matched = "defaults"

        for site in sites:
            if not isinstance(site, dict):
                continue
            patterns = site.get("match")
            if isinstance(patterns, str):
                patterns = [patterns]
            if not patterns:
                continue

            hit = any(
                fnmatch.fnmatch(domain, p) or fnmatch.fnmatch(url, p)
                for p in patterns
            )
            if hit:
                merged = {**merged, **{k: v for k, v in site.items() if k != "match"}}
                matched = ", ".join(patterns)
                break

        return Rule(
            subfolder=str(merged.get("subfolder", "")),
            filename=str(merged.get("filename", "{{ title }}")),
            template=str(merged.get("template", "default.md.j2")),
            on_conflict=str(merged.get("on_conflict", "uniquify")),
            download_assets=bool(merged.get("download_assets", False)),
            ocr_images=bool(merged.get("ocr_images", True)),
            category=str(merged.get("category", "")),
            subcategory=str(merged.get("subcategory", "")),
            tags=list(merged.get("tags") or []),
            matched=matched,
        )

    @property
    def error(self) -> str | None:
        self.reload()
        return self._error

    @property
    def site_count(self) -> int:
        self.reload()
        with self._lock:
            return len(self._data["sites"])


store = ConfigStore()
