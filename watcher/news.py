"""Restock-Ankündigungen aus RSS-Feeds (Google News, mydealz …)."""
from __future__ import annotations

import hashlib
import html as htmllib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import fetch

MAX_AGE = timedelta(days=21)
FIRST_RUN_LIMIT = 10
SEEN_CAP = 1000


@dataclass
class NewsItem:
    id: str
    title: str
    link: str
    published: datetime | None
    source: str


def parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    items = []
    for it in root.iter("item"):
        title = htmllib.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link or title).strip()
        pub = None
        if raw := it.findtext("pubDate"):
            try:
                pub = parsedate_to_datetime(raw)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pub = None
        desc = re.sub(r"<[^>]+>", " ", htmllib.unescape(it.findtext("description") or ""))
        items.append(NewsItem(
            id=hashlib.sha1(guid.encode()).hexdigest()[:16],
            title=title, link=link, published=pub, source=f"{source}\n{desc}",
        ))
    return items


def _other_product(text: str, title: str, cfg: dict) -> bool:
    """Andere 30-Jahre-Produkte (Blister, Tins …) – außer die Überschrift nennt auch die Box.

    Beispiel: „Kaufland lokal: 30 Jahre Top Trainer Box & 2er Blister“ ist relevant.
    """
    return (any(w in text for w in cfg.get("other_products_any", []))
            and not any(w in title for w in cfg["type_any"]))


def relevant(item: NewsItem, cfg: dict) -> bool:
    """Nur Meldungen zur Top-Trainer-/Elite-Trainer-Box, nicht zu anderen 30-Jahre-Produkten."""
    title = item.title.lower()
    text = f"{title} {item.source}".lower()
    if any(w in text for w in cfg.get("exclude_any", [])) or _other_product(text, title, cfg):
        return False
    return any(w in title for w in cfg["must_any"]) and any(w in text for w in cfg["type_any"])


def relevant_title(title: str, cfg: dict) -> bool:
    """Grobprüfung für bereits gespeicherte Meldungen (nur die Überschrift ist bekannt)."""
    t = title.lower()
    return (any(w in t for w in cfg["must_any"]) and not any(w in t for w in cfg.get("exclude_any", []))
            and not _other_product(t, t, cfg))


def check(cfg: dict, state: dict, now: datetime | None = None) -> tuple[list[NewsItem], list[str]]:
    """Neue relevante Meldungen und Fehlermeldungen je Feed."""
    now = now or datetime.now(timezone.utc)
    if state.get("news_filter") != 2:  # Filter geändert → Meldungen der letzten Wochen neu bewerten
        state["news_seen"], state["news_filter"] = [], 2
    seen: list[str] = state.setdefault("news_seen", [])
    first_run = not seen and not state.get("news_initialized")
    fresh: list[NewsItem] = []
    errors: list[str] = []
    for url in cfg.get("feeds", []):
        try:
            items = parse_feed(fetch.get(url), url.split("/")[2])
        except (fetch.FetchError, ET.ParseError) as e:
            errors.append(f"{url.split('/')[2]}: {e}")
            continue
        hits = sum(relevant(i, cfg) for i in items)
        print(f"News-Feed {url.split('/')[2]}: {len(items)} Einträge, {hits} passend")
        for item in items:
            if item.id in seen or any(f.id == item.id for f in fresh):
                continue
            seen.append(item.id)
            if item.published and now - item.published > MAX_AGE:
                continue
            if relevant(item, cfg):
                fresh.append(item)
    state["news_initialized"] = True
    del seen[:-SEEN_CAP]
    fresh.sort(key=lambda i: i.published or now, reverse=True)
    if first_run:
        fresh = fresh[:FIRST_RUN_LIMIT]
    for item in fresh:  # Quelle für die Mail wieder auf den Feed-Host kürzen
        item.source = item.source.split("\n", 1)[0]
    return fresh, errors
