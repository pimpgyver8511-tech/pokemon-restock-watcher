import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from watcher import fetch, main, news
from watcher.parse import (IN_STOCK, OUT_OF_STOCK, PREORDER, extract, parse_price,
                           product_links, shopify_offer)

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())
P = CFG["product"]
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

JSONLD_PAGE = """<html><head><title>Top-Trainer-Box 30 Jahre | MediaMarkt</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product",
"name":"POKÉMON COMPANY INTERNATIONAL Top-Trainer-Box 30 Jahre Sammelkarten","gtin13":"0196214144842",
"offers":{"@type":"Offer","price":"59.99","priceCurrency":"EUR",
"availability":"https://schema.org/%s","seller":{"@type":"Organization","name":"MediaMarkt"}}}</script>
</head><body>Ähnliche Produkte: In den Warenkorb</body></html>"""

MICRODATA_PAGE = """<html><head><title>Pokémon 30 Jahre Top-Trainer-Box – Spiele-Riese</title></head>
<body><div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Pokémon 30 Jahre Top-Trainer-Box</h1>
<span itemprop="price" content="64,90">64,90 €</span>
<link itemprop="availability" href="https://schema.org/PreOrder"></div></body></html>"""

TEXT_PAGE = """<html><head><title>Pokémon 30 Jahre Top-Trainer-Box</title>
<meta property="product:price:amount" content="54.95"></head>
<body><p>EAN 0196214144842</p><button>Leider ausverkauft</button></body></html>"""

OTHER_PRODUCT = """<html><head><title>Pokémon Karmesin & Purpur Top-Trainer-Box</title>
<script type="application/ld+json">{"@type":"Product","name":"Pokémon Top-Trainer-Box Karmesin",
"offers":{"price":"49.99","availability":"InStock"}}</script></head></html>"""

SEARCH_PAGE = """<html><body>
<a href="/de/product/_pokemon-top-trainer-box-30-jahre-sammelkarten-2087300.html">Top-Trainer-Box 30 Jahre</a>
<a href="/de/product/_pokemon-top-trainer-box-karmesin-1.html">Top-Trainer-Box Karmesin</a>
<a href="https://evil.example/pokemon-30-jahre-top-trainer-box">extern</a></body></html>"""

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Pokémon 30 Jahre: Top-Trainer-Box wieder verfügbar bei Müller</title><link>https://n/1</link>
<guid>1</guid><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>Neue Fußballschuhe im Angebot</title><link>https://n/2</link><guid>2</guid></item>
<item><title>30 Jahre Top-Trainer-Box Restock (alt)</title><link>https://n/3</link>
<guid>3</guid><pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""


class ParseTests(unittest.TestCase):
    def test_price(self):
        self.assertEqual(parse_price("59,99 €"), 59.99)
        self.assertEqual(parse_price("1.059,99"), 1059.99)
        self.assertEqual(parse_price("59.99"), 59.99)
        self.assertEqual(parse_price(64), 64.0)
        self.assertEqual(parse_price("1,059.99"), 1059.99)
        self.assertIsNone(parse_price("n/a"))

    def test_jsonld(self):
        o = extract(JSONLD_PAGE % "InStock", P)
        self.assertEqual((o.price, o.availability, o.seller), (59.99, IN_STOCK, "MediaMarkt"))
        # Strukturierte Angabe "ausverkauft" schlägt den Text "In den Warenkorb" der Empfehlungen.
        self.assertEqual(extract(JSONLD_PAGE % "OutOfStock", P).availability, OUT_OF_STOCK)

    def test_microdata(self):
        o = extract(MICRODATA_PAGE, P)
        self.assertEqual((o.price, o.availability), (64.90, PREORDER))

    def test_meta_and_text_fallback(self):
        o = extract(TEXT_PAGE, P)
        self.assertEqual((o.price, o.availability), (54.95, OUT_OF_STOCK))

    def test_other_product_ignored(self):
        self.assertIsNone(extract(OTHER_PRODUCT, P))

    def test_search_links(self):
        links = product_links(SEARCH_PAGE, "https://www.mediamarkt.at/de/search.html?query=x", P)
        self.assertEqual(links, ["https://www.mediamarkt.at/de/product/_pokemon-top-trainer-box-30-jahre-sammelkarten-2087300.html"])

    def test_shopify(self):
        o = shopify_offer({"title": "Pokémon 30 Jahre Top-Trainer-Box", "available": True, "price": 6999,
                           "variants": [{"barcode": "196214144842"}]}, P)
        self.assertEqual((o.price, o.availability), (69.99, IN_STOCK))


class NewsTests(unittest.TestCase):
    def test_filter_dedupe_age(self):
        state = {}
        with mock.patch.object(fetch, "get", return_value=RSS):
            items, errors = news.check({**CFG["news"], "feeds": ["https://feed.example/rss"]}, state, NOW)
            self.assertEqual([i.link for i in items], ["https://n/1"])
            self.assertEqual(errors, [])
            again, _ = news.check({**CFG["news"], "feeds": ["https://feed.example/rss"]}, state, NOW)
            self.assertEqual(again, [])


class FlowTests(unittest.TestCase):
    """Zustandswechsel: nur bei Änderung mailen."""

    def setUp(self):
        self.cfg = {**CFG, "shops": [{"name": "MediaMarkt", "country": "DE", "ships_to_de": "yes",
                                      "urls": ["https://mm.example/p"]}]}
        self.state = main.load_state(Path("/nonexistent"))
        self.state["last_report"] = NOW.isoformat()

    def run_with(self, page, now=NOW):
        with mock.patch.object(fetch, "get", return_value=page):
            return main.run(self.cfg, self.state, now, with_news=False)

    def test_transitions(self):
        subject, _ = self.run_with(JSONLD_PAGE % "OutOfStock")
        self.assertIsNone(subject)

        subject, body = self.run_with(JSONLD_PAGE % "InStock")
        self.assertIn("🚨", subject)
        self.assertIn("MediaMarkt", subject)
        self.assertIn("59,99 €", body)

        subject, _ = self.run_with(JSONLD_PAGE % "InStock")
        self.assertIsNone(subject, "gleicher Stand darf keine zweite Mail auslösen")

        subject, _ = self.run_with(JSONLD_PAGE % "OutOfStock")
        self.assertIsNone(subject, "Ausverkauf allein löst keine Mail aus")
        self.assertFalse(self.state["shops"]["MediaMarkt"]["hit"])

        subject, _ = self.run_with(JSONLD_PAGE % "InStock")
        self.assertIn("🚨", subject, "erneuter Restock wird wieder gemeldet")

    def test_out_of_price_range(self):
        subject, _ = self.run_with((JSONLD_PAGE % "InStock").replace("59.99", "169.99"))
        self.assertIsNone(subject)

    def test_failures_alert_once(self):
        def boom(url, timeout=25):
            raise fetch.FetchError("HTTP 403")
        subjects = []
        with mock.patch.object(fetch, "get", side_effect=boom):
            for _ in range(CFG["failure_alert_after"] + 3):
                subjects.append(main.run(self.cfg, self.state, NOW, with_news=False)[0])
        self.assertEqual(sum(s is not None for s in subjects), 1)
        self.assertIn("⚠️", [s for s in subjects if s][0])

    def test_weekly_report(self):
        self.state["last_report"] = (NOW - timedelta(days=8)).isoformat()
        subject, body = self.run_with(JSONLD_PAGE % "OutOfStock")
        self.assertIn("Wochenübersicht", subject)
        self.assertIn("MediaMarkt [DE]: nicht verfügbar", body)


if __name__ == "__main__":
    unittest.main()
