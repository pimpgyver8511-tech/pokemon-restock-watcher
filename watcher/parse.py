"""Extrahiert Produktname, Preis und Verfügbarkeit aus Shop-Seiten.

Reihenfolge der Quellen (zuverlässigste zuerst):
  1. schema.org JSON-LD (<script type="application/ld+json">)
  2. schema.org Microdata (itemprop=...)
  3. Open-Graph-/Product-Meta-Tags
  4. Text-Heuristik (nur Verfügbarkeit, geringste Sicherheit)
"""
from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

IN_STOCK = "in_stock"
PREORDER = "preorder"
OUT_OF_STOCK = "out_of_stock"
UNKNOWN = "unknown"

ORDERABLE = {IN_STOCK, PREORDER}


@dataclass
class Offer:
    name: str | None = None
    price: float | None = None
    currency: str | None = None
    availability: str = UNKNOWN
    gtins: set[str] = field(default_factory=set)
    seller: str | None = None
    available_from: str | None = None  # Liefertermin bei Vorbestellungen, "TT.MM.JJJJ" oder "TT.MM."
    source: str = ""  # jsonld | microdata | meta | text | shopify


# --------------------------------------------------------------------------- helpers

def parse_price(value) -> float | None:
    """'59,99 €' / '1.059,99' / '59.99' / 59.99 -> float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        # Das zuletzt stehende Zeichen ist das Dezimaltrennzeichen.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + "." + tail if len(tail) <= 2 else s.replace(",", "")
    elif s.count(".") > 1 or (s.count(".") == 1 and len(s.rpartition(".")[2]) == 3):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def normalize_availability(value) -> str:
    if not value:
        return UNKNOWN
    v = str(value).lower().rsplit("/", 1)[-1]
    if v in ("instock", "in_stock", "limitedavailability", "onlineonly", "instoreonly", "true"):
        return IN_STOCK
    if v in ("preorder", "presale", "backorder"):
        return PREORDER
    if v in ("outofstock", "out_of_stock", "soldout", "discontinued", "false", "oos"):
        return OUT_OF_STOCK
    return UNKNOWN


def _norm_gtin(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 8:
        return None
    return digits.zfill(13)  # UPC-12/EAN-13 vergleichbar machen


def _types(node) -> list[str]:
    t = node.get("@type", [])
    return [str(x).lower() for x in (t if isinstance(t, list) else [t])]


# --------------------------------------------------------------------------- JSON-LD

_LDJSON_RE = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.I | re.S,
)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _offers_from_product(p: dict) -> list[Offer]:
    base_gtins = {g for k in ("gtin13", "gtin", "gtin12", "gtin14", "ean", "isbn")
                  if (g := _norm_gtin(p.get(k)))}
    name = p.get("name")
    raw = p.get("offers") or []
    raw = raw if isinstance(raw, list) else [raw]
    result = []
    for o in raw:
        if not isinstance(o, dict):
            continue
        nested = o.get("offers")
        if "aggregateoffer" in _types(o) and isinstance(nested, list) and nested:
            result.extend(_offers_from_product({**p, "offers": nested}))
            continue
        seller = o.get("seller")
        if isinstance(seller, dict):
            seller = seller.get("name")
        price = o.get("price")
        if price is None and isinstance(o.get("priceSpecification"), dict):
            price = o["priceSpecification"].get("price")
        if price is None:
            price = o.get("lowPrice")
        gt = set(base_gtins)
        if g := _norm_gtin(o.get("gtin13") or o.get("gtin")):
            gt.add(g)
        result.append(Offer(
            name=name, price=parse_price(price), currency=o.get("priceCurrency"),
            availability=normalize_availability(o.get("availability")),
            gtins=gt, seller=seller, source="jsonld",
            available_from=_iso_to_de(o.get("availabilityStarts")),
        ))
    if not result:
        result.append(Offer(name=name, gtins=base_gtins, source="jsonld"))
    return result


def offers_from_jsonld(html: str) -> list[Offer]:
    offers: list[Offer] = []
    for raw in _LDJSON_RE.findall(html):
        raw = htmllib.unescape(raw.strip()).strip().rstrip(";")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Häufig: Steuerzeichen/Zeilenumbrüche in Strings.
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", raw))
            except json.JSONDecodeError:
                continue
        for node in _walk(data):
            types = _types(node)
            if "product" in types or "productgroup" in types:
                offers.extend(_offers_from_product(node))
    return offers


# --------------------------------------------------------------------------- Microdata / Meta

class _AttrCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.itemprops: dict[str, list[str]] = {}
        self.meta: dict[str, str] = {}
        self.links: list[tuple[str, str]] = []
        self.title = ""
        self._in_title = False
        self._capture: str | None = None
        self._a_href: str | None = None
        self._a_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        prop = a.get("itemprop")
        if prop:
            value = a.get("content") or a.get("href") or a.get("value")
            if value:
                self.itemprops.setdefault(prop.lower(), []).append(value)
            elif tag not in ("div", "section", "article", "ul", "body", "main"):
                self._capture = prop.lower()
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        if tag == "title":
            self._in_title = True
        if tag == "a" and a.get("href"):
            self._a_href = a["href"]
            self._a_text = [a.get("title", ""), a.get("aria-label", "")]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._a_href:
            self.links.append((self._a_href, " ".join(self._a_text)))
            self._a_href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._capture and data.strip():
            self.itemprops.setdefault(self._capture, []).append(data.strip())
            self._capture = None
        if self._a_href:
            self._a_text.append(data)


def collect(html: str) -> _AttrCollector:
    c = _AttrCollector()
    try:
        c.feed(html)
        c.close()
    except Exception:  # kaputtes HTML: nehmen, was bis dahin gesammelt wurde
        pass
    return c


def offers_from_microdata(c: _AttrCollector) -> list[Offer]:
    ip = c.itemprops
    if not ("price" in ip or "availability" in ip):
        return []
    gtins = {g for k in ("gtin13", "gtin", "gtin12", "ean")
             for v in ip.get(k, []) if (g := _norm_gtin(v))}
    return [Offer(
        name=(ip.get("name") or [None])[0],
        price=parse_price((ip.get("price") or [None])[0]),
        currency=(ip.get("pricecurrency") or [None])[0],
        availability=normalize_availability((ip.get("availability") or [None])[0]),
        gtins=gtins, source="microdata",
    )]


def offers_from_meta(c: _AttrCollector) -> list[Offer]:
    m = c.meta
    price = m.get("product:price:amount") or m.get("og:price:amount")
    avail = m.get("product:availability") or m.get("og:availability")
    if not (price or avail):
        return []
    avail_n = normalize_availability(avail.replace(" ", "") if avail else None)
    return [Offer(
        name=m.get("og:title") or c.title.strip() or None,
        price=parse_price(price), currency=m.get("product:price:currency"),
        availability=avail_n, source="meta",
    )]


# --------------------------------------------------------------------------- Liefertermin

_DATE_RE = re.compile(
    r"(?:verfügbar ab|lieferbar ab|erhältlich ab|versand ab|erscheint am|erscheint|erscheinungstermin|"
    r"erscheinungsdatum|release|releasedatum|evt|vsl\.? ab|voraussichtlich ab|voraussichtlich)"
    r"\s*:?\s*(?:am\s*)?(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}|\d{2})\b)?",
    re.I,
)


def _iso_to_de(value) -> str | None:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m[3]}.{m[2]}.{m[1]}" if m else None


def available_from_text(text: str) -> str | None:
    """Erstes plausibles "verfügbar ab 09.10."-Datum im Text."""
    for m in _DATE_RE.finditer(text):
        day, month, year = int(m[1]), int(m[2]), m[3]
        if not (1 <= day <= 31 and 1 <= month <= 12):
            continue
        if year:
            year = int(year) + (2000 if len(year) == 2 else 0)
            return f"{day:02d}.{month:02d}.{year}"
        return f"{day:02d}.{month:02d}."
    return None


def _plain_text(html: str) -> str:
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", text)))


# --------------------------------------------------------------------------- Text-Heuristik

_NEG = [
    "ausverkauft", "nicht verfügbar", "nicht lieferbar", "derzeit nicht", "zurzeit nicht",
    "vergriffen", "nicht auf lager", "nicht mehr verfügbar", "online nicht", "leider nicht",
    "out of stock", "sold out", "currently unavailable", "agotado", "esaurito", "épuisé",
    "uitverkocht", "benachrichtigen, wenn", "notify me",
]
_PRE = ["vorbestellen", "vorbestellbar", "pre-order", "preorder", "reservar"]
_POS = [
    "in den warenkorb", "add to cart", "add to basket", "sofort lieferbar", "auf lager",
    "sofort verfügbar", "online verfügbar", "añadir al carrito", "in winkelwagen",
    "aggiungi al carrello", "ajouter au panier",
]


def availability_from_text(html: str) -> str:
    text = _plain_text(html).lower()
    if any(p in text for p in _NEG):
        return OUT_OF_STOCK
    if any(p in text for p in _PRE):
        return PREORDER
    if any(p in text for p in _POS):
        return IN_STOCK
    return UNKNOWN


# --------------------------------------------------------------------------- Produkt-Abgleich

def matches_product(text: str | None, gtins: set[str], product_cfg: dict) -> bool:
    wanted = {_norm_gtin(e) for e in product_cfg["eans"]}
    if gtins & wanted:
        return True
    if not text:
        return False
    t = text.lower().replace(" ", " ")
    if any(x in t for x in product_cfg.get("exclude_any", [])):
        return False
    return (any(x in t for x in product_cfg["name_any"])
            and any(x in t for x in product_cfg["type_any"]))


def page_mentions_ean(html: str, product_cfg: dict) -> bool:
    return any(e.lstrip("0") in html for e in product_cfg["eans"])


def extract(html: str, product_cfg: dict) -> Offer | None:
    """Bestes passendes Angebot einer Produktseite oder None, wenn das Produkt nicht erkennbar ist."""
    c = collect(html)
    page_name = c.meta.get("og:title") or c.title
    ean_on_page = page_mentions_ean(html, product_cfg)

    candidates = offers_from_jsonld(html) + offers_from_microdata(c) + offers_from_meta(c)
    matching = [o for o in candidates
                if matches_product(o.name, o.gtins, product_cfg)
                or (o.name is None and (ean_on_page or matches_product(page_name, set(), product_cfg)))]

    if not matching:
        if not (ean_on_page or matches_product(page_name, set(), product_cfg)):
            return None
        matching = [Offer(name=page_name.strip() or None, source="text")]

    # Strukturierte Daten zusammenführen: erste bekannte Angabe gewinnt.
    best = Offer(name=next((o.name for o in matching if o.name), None), source=matching[0].source)
    for o in matching:
        best.gtins |= o.gtins
        if best.price is None and o.price is not None:
            best.price, best.currency = o.price, o.currency
        if best.availability == UNKNOWN and o.availability != UNKNOWN:
            best.availability = o.availability
        if best.seller is None and o.seller:
            best.seller = o.seller
        if best.available_from is None and o.available_from:
            best.available_from = o.available_from
    if best.availability == UNKNOWN:
        best.availability = availability_from_text(html)
        best.source += "+text"
    if best.available_from is None:
        best.available_from = available_from_text(f"{best.name or ''} {_plain_text(html)}")
    return best


_ASSET_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|svg|pdf|css|js|ico|mp4|zip)(?:$|\?)", re.I)


def is_asset(url: str) -> bool:
    """Bilder, PDFs usw. sind keine Produktseiten."""
    return bool(_ASSET_RE.search(urlparse(url).path + ("?" if urlparse(url).query else "")))


def product_links(html: str, base_url: str, product_cfg: dict, limit: int = 3) -> list[str]:
    """Links auf einer Suchergebnisseite, die wie das gesuchte Produkt aussehen."""
    c = collect(html)
    host = urlparse(base_url).netloc
    seen: list[str] = []
    for href, text in c.links:
        url = urljoin(base_url, htmllib.unescape(href)).split("#")[0]
        if urlparse(url).netloc != host or url in seen or url == base_url or is_asset(url):
            continue
        slug = re.sub(r"[-_/+%]+", " ", urlparse(url).path.lower()).replace("pok c3 a9mon", "pokemon")
        if matches_product(f"{text} {slug}", set(), product_cfg) or any(
                e.lstrip("0") in url for e in product_cfg["eans"]):
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen


def shopify_offer(data: dict, product_cfg: dict) -> Offer | None:
    """Auswertung von https://shop/products/<handle>.js"""
    title = data.get("title")
    variants = data.get("variants") or []
    gtins = {g for v in variants if (g := _norm_gtin(v.get("barcode")))}
    if not matches_product(title, gtins, product_cfg):
        return None
    price = data.get("price")
    body = re.sub(r"<[^>]+>", " ", htmllib.unescape(data.get("description") or ""))
    return Offer(
        available_from=available_from_text(f"{title} {body}"),
        name=title,
        price=price / 100 if isinstance(price, (int, float)) else parse_price(price),
        currency="EUR",
        availability=IN_STOCK if data.get("available") else OUT_OF_STOCK,
        gtins=gtins, source="shopify",
    )
