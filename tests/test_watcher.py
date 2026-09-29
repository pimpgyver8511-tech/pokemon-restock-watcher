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
<item><title>30th Celebration Elite Trainer Box restock for $49.99 at Target</title><link>https://n/4</link><guid>4</guid></item>
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
        self.state["last_digest"] = "2026-09-29"  # Tagesmail für NOW schon verschickt

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

    def test_failures_warn_once_in_digest(self):
        def boom(url, timeout=25):
            raise fetch.FetchError("HTTP 403")
        with mock.patch.object(fetch, "get", side_effect=boom):
            for _ in range(CFG["failure_alert_after"] + 3):
                self.assertIsNone(main.run(self.cfg, self.state, NOW, with_news=False)[0])
        self.assertEqual(sum("⚠️" in p for p in self.state["pending"]), 1)

    def test_marketplace_seller_ignored(self):
        self.cfg["shops"][0]["seller_any"] = ["mediamarkt"]
        page = (JSONLD_PAGE % "InStock").replace('"name":"MediaMarkt"', '"name":"Karten-Profi GmbH"')
        subject, _ = self.run_with(page)
        self.assertIsNone(subject)
        self.assertEqual(self.state["shops"]["MediaMarkt"]["status"], "not_listed")

    def test_search_list_and_links(self):
        self.cfg["shops"][0] = {"name": "MediaMarkt", "country": "DE", "ships_to_de": "yes",
                                "search": ["https://mm.example/s?q={ean}", "https://mm.example/cat"]}
        pages = {"https://mm.example/cat": '<a href="/p/top-trainer-box-30-jahre">x</a>',
                 "https://mm.example/p/top-trainer-box-30-jahre": JSONLD_PAGE % "InStock"}
        with mock.patch.object(fetch, "get", side_effect=lambda u, timeout=25: pages.get(u, "<html></html>")):
            subject, _ = main.run(self.cfg, self.state, NOW, with_news=False)
        self.assertIn("🚨", subject)
        self.assertEqual(self.state["discovered"]["MediaMarkt"], ["https://mm.example/p/top-trainer-box-30-jahre"])

    def test_daily_digest(self):
        self.state["last_digest"] = "2026-09-28"
        early = datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)  # 06:00 Uhr – noch vor digest_hour
        subject, _ = self.run_with(JSONLD_PAGE % "OutOfStock", early)
        self.assertIsNone(subject)
        subject, body = self.run_with(JSONLD_PAGE % "OutOfStock")
        self.assertIn("Tagesübersicht 29.09.", subject)
        self.assertIn("MediaMarkt [DE]: nicht verfügbar", body)
        self.assertIsNone(self.run_with(JSONLD_PAGE % "OutOfStock")[0], "nur eine Tagesmail pro Tag")

    def test_news_and_warnings_wait_for_digest(self):
        self.state["pending"] = ["[29.09. 10:00] 📰 Restock bei Müller"]
        self.state["last_digest"] = "2026-09-28"
        subject, body = self.run_with(JSONLD_PAGE % "OutOfStock")
        self.assertIn("1 neue Meldung", subject)
        self.assertIn("Restock bei Müller", body)
        self.assertEqual(self.state["pending"], [])


class ScheduleTests(unittest.TestCase):
    def test_overrides(self):
        cfg = main.apply_overrides(yaml.safe_load(yaml.safe_dump(CFG)), {
            "WATCH_INTERVAL_MIN": "30", "WATCH_FROM_HOUR": "7", "WATCH_TO_HOUR": "23",
            "PRICE_MAX": "70", "INSTANT_ALERTS": "false", "DIGEST_HOUR": "", "PRICE_MIN": "abc"})
        self.assertEqual(cfg["schedule"], {"interval_minutes": 30, "active_from": 7, "active_to": 23})
        self.assertEqual((cfg["price"]["min"], cfg["price"]["max"]), (50.0, 70.0))
        self.assertFalse(cfg["instant_alerts"])
        self.assertEqual(cfg["digest_hour"], CFG["digest_hour"])

    def test_is_due(self):
        cfg = {"schedule": {"interval_minutes": 30, "active_from": 7, "active_to": 23}}
        at = lambda h, m=0: datetime(2026, 9, 29, h - 2, m, tzinfo=timezone.utc)  # Berlin = UTC+2
        self.assertTrue(main.is_due(cfg, {}, at(12))[0])
        self.assertFalse(main.is_due(cfg, {}, at(6))[0])
        self.assertFalse(main.is_due(cfg, {}, at(23, 30))[0])
        self.assertFalse(main.is_due(cfg, {"last_check": at(12).isoformat()}, at(12, 20))[0])
        self.assertTrue(main.is_due(cfg, {"last_check": at(12).isoformat()}, at(12, 28))[0])
        night = {"schedule": {"interval_minutes": 10, "active_from": 22, "active_to": 7}}
        self.assertTrue(main.is_due(night, {}, at(23))[0])
        self.assertTrue(main.is_due(night, {}, at(3))[0])
        self.assertFalse(main.is_due(night, {}, at(12))[0])


if __name__ == "__main__":
    unittest.main()
