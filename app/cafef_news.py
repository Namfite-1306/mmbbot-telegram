"""Best-effort, source-linked CafeF company headlines (not trading data)."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests


@dataclass(frozen=True)
class NewsItem:
    title: str
    published: str
    url: str


class _CompanyNewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[NewsItem] = []
        self._li_depth = 0
        self._capture: str | None = None
        self._title = ""
        self._published = ""
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "li":
            if self._li_depth == 0:
                self._title = self._published = self._href = ""
            self._li_depth += 1
        if not self._li_depth:
            return
        classes = set((attr.get("class") or "").split())
        if tag == "span" and "timeTitle" in classes:
            self._capture = "published"
        elif tag == "a" and "docnhanhTitle" in classes:
            self._capture = "title"
            self._href = attr.get("href") or ""

    def handle_data(self, data: str) -> None:
        if self._capture == "published":
            self._published += data
        elif self._capture == "title":
            self._title += data

    def handle_endtag(self, tag: str) -> None:
        if tag in {"span", "a"}:
            self._capture = None
        if tag == "li" and self._li_depth:
            self._li_depth -= 1
            if self._li_depth == 0 and self._title.strip() and self._href:
                url = urljoin("https://cafef.vn/", self._href)
                parsed = urlparse(url)
                if parsed.scheme == "https" and parsed.hostname == "cafef.vn":
                    self.items.append(NewsItem(self._title.strip(), self._published.strip(), url))


def fetch_company_news(symbol: str, limit: int = 2) -> list[NewsItem]:
    """Return observed headlines only; callers should tolerate network errors."""
    if not symbol.isalnum() or not 1 <= len(symbol) <= 10:
        return []
    url = f"https://cafef.vn/du-lieu/tin-doanh-nghiep/{symbol.lower()}/event.chn"
    response = requests.get(url, timeout=7, headers={"User-Agent": "Mozilla/5.0"})
    response.raise_for_status()
    parser = _CompanyNewsParser()
    parser.feed(response.text)
    return parser.items[:limit]
