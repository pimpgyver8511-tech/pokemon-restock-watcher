import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from watcher import fetch, flyers, main, news, ocr, shopify, stores
from watcher.parse import (IN_STOCK, OUT_OF_STOCK, PREORDER, available_from_text, extract, parse_price,
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
<a href="https://evil.example/pokemon-30-jahre-top-trainer-box">extern</a>
<a href="/images/Pokemon-30-Jahre-Top-Trainer-Box-Deutsch.jpg">Bild</a></body></html>"""

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Pokémon 30 Jahre: Top-Trainer-Box wieder verfügbar bei Müller</title><link>https://n/1</link>
<guid>1</guid><pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate></item>
<item><title>30th Celebration Elite Trainer Box restock for $49.99 at Target</title><link>https://n/4</link><guid>4</guid></item>
<item><title>Pokemon 30th Celebration Tech Sticker Kollektion</title><link>https://n/5</link><guid>5</guid>
<description>Passend zur Top-Trainer-Box</description></item>
<item><title>Rain World Deluxe Edition</title><link>https://n/6</link><guid>6</guid><description>30 Jahre Jubiläum Top Trainer</description></item>
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

    def test_available_from(self):
        page = (MICRODATA_PAGE.replace("</div></body>", "<p>Vorbestellung – verfügbar ab 9.10.</p></div></body>"))
        self.assertEqual(extract(page, P).available_from, "09.10.")
        self.assertEqual(available_from_text("Erscheinungstermin: 16.09.2026"), "16.09.2026")
        self.assertEqual(available_from_text("Pokémon 30 Jahre TTB | EVT 16.09.26"), "16.09.2026")
        self.assertIsNone(available_from_text("Versand in 1-3 Tagen, 30.5 cm"))
        ld = (JSONLD_PAGE % "PreOrder").replace('"priceCurrency"', '"availabilityStarts":"2026-10-09","priceCurrency"')
        o = extract(ld, P)
        self.assertEqual((o.availability, o.available_from), (PREORDER, "09.10.2026"))

    def test_shopify(self):
        o = shopify_offer({"title": "Pokémon 30 Jahre Top-Trainer-Box", "available": True, "price": 6999,
                           "variants": [{"barcode": "196214144842"}]}, P)
        self.assertEqual((o.price, o.availability), (69.99, IN_STOCK))


class FetchTests(unittest.TestCase):
    def test_js_challenge_page_is_blocked(self):
        resp = mock.Mock(status_code=200, text="<html><head><title>shop.com</title><script>x()</script></head></html>")
        with mock.patch.object(fetch, "_get", return_value=resp), mock.patch.object(fetch.time, "sleep"):
            with self.assertRaises(fetch.FetchError):
                fetch.get("https://shop.example/p")
        resp.text = JSONLD_PAGE % "InStock" + '<a href="/">Start</a>'
        with mock.patch.object(fetch, "_get", return_value=resp), mock.patch.object(fetch.time, "sleep"):
            self.assertIn("MediaMarkt", fetch.get("https://shop.example/p"))


class NewsTests(unittest.TestCase):
    def test_filter_dedupe_age(self):
        state = {}
        with mock.patch.object(fetch, "get", return_value=RSS):
            items, errors = news.check({**CFG["news"], "feeds": ["https://feed.example/rss"]}, state, NOW)
            self.assertEqual([i.link for i in items], ["https://n/1"])
            self.assertEqual(errors, [])
            again, _ = news.check({**CFG["news"], "feeds": ["https://feed.example/rss"]}, state, NOW)
            self.assertEqual(again, [])

    def test_box_with_blister_is_relevant(self):
        cfg = yaml.safe_load((main.ROOT / "config.yaml").read_text())["news"]
        item = lambda t: news.NewsItem(id=t, title=t, link="https://x", published=None, source="mydealz")
        self.assertTrue(news.relevant(item("[Kaufland lokal] Pokémon 30 Jahre Top Trainer Box & 2er Blister 55€"), cfg))
        self.assertTrue(news.relevant(item("Pokemon 30 Jahre Jubiläum Trainertasche & 2er Blister |Kaufland Leipzig| 55,00€"), cfg))
        self.assertFalse(news.relevant(item("Pokémon 30 Jahre 2er Blister bei Kaufland"), cfg))
        self.assertTrue(news.relevant_title("Pokemon 30 Jahre Jubiläum Trainertasche & 2er Blister", cfg))


class ExtraProductTests(unittest.TestCase):
    """Weitere Produkte (more_products), z. B. das Booster-Bundle."""
    SEARCH = '<html><a href="/p/bundle">Pokémon 30 Jahre Booster Bundle (deutsch)</a><a href="/p/ttb">x</a></html>'
    PAGE = """<html><head><title>Pokémon 30 Jahre Booster-Bundle</title>
<script type="application/ld+json">{"@type":"Product","name":"Pokémon 30 Jahre Booster-Bundle",
"offers":{"@type":"Offer","price":"39.99","priceCurrency":"EUR","availability":"https://schema.org/InStock"}}</script>
</head><body>In den Warenkorb</body></html>"""

    def fake_get(self, url, timeout=25, headers=None):
        if "?q=" in url:
            return self.SEARCH
        if url.endswith("/p/bundle"):
            return self.PAGE
        return "<html><a href='/'>leer</a>" + " " * 50000 + "</html>"

    def test_bundle_hit_and_snapshot(self):
        cfg = {**CFG, "stores": None, "flyers": None, "shopify_search": None,
               "shops": [{"name": "Testshop", "country": "DE", "ships_to_de": "yes",
                          "urls": ["https://shop.example/p/ttb"], "search": "https://shop.example/?q={ean}"}]}
        state = main.load_state(Path("/nonexistent"))
        state["last_digest"] = "2026-09-29"
        with mock.patch.object(fetch, "get", side_effect=self.fake_get) as get:
            subject, body = main.run(cfg, state, NOW, with_news=False)
        called = [c.args[0] for c in get.call_args_list]
        self.assertIn("https://shop.example/?q=30+Jahre+Booster+Bundle", called)
        self.assertIn("Testshop (Booster-Bundle)", subject)
        self.assertTrue(state["shops"]["bundle|Testshop"]["hit"])
        snap = main.snapshot(cfg, state, NOW)
        self.assertEqual([p["id"] for p in snap["products"]], ["main", "bundle"])
        bundle = [s for s in snap["shops"] if s["product"] == "bundle"]
        self.assertEqual((bundle[0]["price"], bundle[0]["in_range"]), (39.99, True))
        main_entry = [s for s in snap["shops"] if s["product"] == "main"][0]
        self.assertFalse(main_entry["in_range"])


class RaffleTests(unittest.TestCase):
    JS = {"title": "Pokémon 30 Jahre Booster-Bundle", "available": True, "price": 4499, "variants": [{"barcode": None}]}

    def run_check(self, html):
        bundle = {"eans": [], "exclude_any": [], **CFG["more_products"][0]}
        with mock.patch.object(fetch, "get_json", return_value=self.JS), mock.patch.object(fetch, "get", return_value=html):
            return main.check_url("https://feen.example/products/bundle", bundle)

    def test_raffle_is_not_orderable(self):
        html = "<main>Verlosung: Dieses Produkt wird nicht direkt gekauft, sondern per Zufallsprinzip verlost.</main>"
        self.assertEqual(self.run_check(html).availability, "out_of_stock")

    def test_raffle_link_in_menu_is_ignored(self):
        html = "<header><a href='/raffle'>Verlosung (Raffle) per Zufallsprinzip</a></header><main>In den Warenkorb</main>"
        self.assertEqual(self.run_check(html).availability, "in_stock")

    def test_preorder_note(self):
        html = "<main>HINWEIS: Dieser Artikel ist derzeit nicht auf Lager, kann aber vorbestellt werden.</main>"
        self.assertEqual(self.run_check(html).availability, "preorder")


class FlowTests(unittest.TestCase):
    """Zustandswechsel: nur bei Änderung mailen."""

    def setUp(self):
        self.cfg = {**CFG, "stores": None, "flyers": None, "shopify_search": None, "shops": [{"name": "MediaMarkt", "country": "DE", "ships_to_de": "yes",
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

    def test_aggregator_never_hit(self):
        self.cfg["shops"][0]["aggregator"] = True
        subject, _ = self.run_with(JSONLD_PAGE % "InStock")
        self.assertIsNone(subject)

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


class SnapshotTests(unittest.TestCase):
    setUp = FlowTests.setUp
    run_with = FlowTests.run_with

    def test_snapshot_links_and_delisting(self):
        self.run_with(JSONLD_PAGE % "InStock")
        snap = main.snapshot(self.cfg, self.state, NOW)
        mm = snap["shops"][0]
        self.assertEqual((mm["status"], mm["price"], mm["in_range"], mm["url"]),
                         ("in_stock", 59.99, True, "https://mm.example/p"))
        self.assertEqual(mm["shop_url"], "https://mm.example/p")

        self.run_with(OTHER_PRODUCT)  # Produkt verschwindet von der Seite
        mm = main.snapshot(self.cfg, self.state, NOW)["shops"][0]
        self.assertEqual((mm["status"], mm["price"], mm["in_range"], mm["url"]), ("not_listed", None, False, None))

    def test_preorder_hit_with_date(self):
        page = (JSONLD_PAGE % "PreOrder").replace('"priceCurrency"', '"availabilityStarts":"2026-10-09","priceCurrency"')
        subject, body = self.run_with(page)
        self.assertIn("🚨", subject)
        self.assertIn("VORBESTELLBAR (Lieferung ab 09.10.2026)", body)
        self.assertEqual(main.snapshot(self.cfg, self.state, NOW)["shops"][0]["available_from"], "09.10.2026")

    def test_shop_link_from_search(self):
        shop = {"search": ["https://s.example/?q={ean}"]}
        self.assertEqual(main.shop_link(shop, P), "https://s.example/?q=0196214144842")


class StoreTests(unittest.TestCase):
    CFG = {"lat": 51.3197, "lng": 12.3714, "radius_km": 10, "chains": ["MediaMarkt"], "queries": ["x"]}

    def fake_gql(self, chain, operation, variables):
        if operation == "GetClosestStoresWithFoundLocation":
            return {"stores": [
                {"outlet_id": 1, "name": "Leipzig Paunsdorf", "position": {"lat": 51.35, "lng": 12.46},
                 "address": {"street": "Paunsdorfer Allee", "houseNumber": "1"}},
                {"outlet_id": 2, "name": "Halle", "position": {"lat": 51.48, "lng": 11.97}}]}
        if operation == "SearchV4":
            return {"searchV4": {"products": [
                {"id": "Media:de:2087300:1", "title": "POKÉMON Top-Trainer-Box 30 Jahre Sammelkarten", "url": "/de/p/x"},
                {"id": "Media:de:111:1", "title": "Pokémon Top-Trainer-Box Karmesin"}]}}
        return {"cofrPickupFeature": [{"id": "Media:de:2087300:1", "pickupStatus": "AVAILABLE_WITHIN_THIRTY_MINUTES"}]}

    def test_store_check(self):
        with mock.patch.object(stores, "_gql", side_effect=self.fake_gql):
            entries, errors = stores.check(self.CFG, P, {})
        self.assertEqual(errors, [])
        self.assertEqual(len(entries), 1, "Halle liegt außerhalb von 10 km")
        e = entries[0]
        self.assertEqual((e["name"], e["status"], e["url"]),
                         ("Leipzig Paunsdorf", "in_stock", "https://www.mediamarkt.de/de/p/x"))
        self.assertLess(e["distance_km"], 10)

    def test_store_errors_do_not_break_run(self):
        with mock.patch.object(stores, "_gql", side_effect=ValueError("kaputt")):
            entries, errors = stores.check(self.CFG, P, {})
        self.assertEqual(entries, [])
        self.assertIn("kaputt", errors[0])


class FlyerTests(unittest.TestCase):
    CFG = {"postal_code": "04275", "lat": 51.3, "lng": 12.37, "queries": ["Pokemon"], "pages": [],
           "related_any": [], "marktguru": False}
    SEARCH = {"searchResults": {"contents": {"brochures": [{"content": {"id": "b1"}}]}}}
    PAGES = {"contents": [{"offers": [
        {"content": {"id": "o1", "publisher": {"name": "Müller"},
                     "parentContent": {"id": "b1", "page": {"number": 3}},
                     "products": [{"brandName": "Pokémon", "name": "Top-Trainer-Box 30 Jahre",
                                   "description": [{"paragraph": "Sammelkartenspiel"}]}],
                     "deals": [{"type": "SALES_PRICE", "min": 54.99}],
                     "publicationProfiles": [{"validity": {"startDate": "2026-09-28T00:00:00",
                                                           "endDate": "2026-10-04T23:59:59"}}]}},
        {"content": {"id": "o2", "publisher": {"name": "Kaufland"},
                     "products": [{"name": "Pokémon Booster"}], "deals": [{"type": "SALES_PRICE", "min": 4.99}]}},
        {"content": {"id": "o3", "products": [{"name": "Krombacher Pils"}]}},
        {"content": {"id": "o4", "products": [{"name": "Funko Adventskalender Pokémon"}]}}]}]}

    def fake_get(self, url, timeout=25, headers=None):
        return json.dumps(self.SEARCH if "/api/search" in url else self.PAGES)

    def test_flyer_offers(self):
        with mock.patch.object(fetch, "get", side_effect=self.fake_get):
            offers, errors = flyers.check(self.CFG, P)
        self.assertEqual(errors, [])
        self.assertEqual([(o["kind"], o["store"], o["price"]) for o in offers],
                         [("exact", "Müller", 54.99), ("pokemon", "Kaufland", 4.99), ("pokemon", None, None)])
        self.assertIn("contentViewer/static/b1", offers[0]["url"])
        self.assertEqual(offers[0]["valid_until"], "2026-10-04")

    def test_publisher_page(self):
        nd = {"props": {"pageProps": {"pageInformation": {"brochures": {
            "publisher": [{"contentId": "s1", "title": "Spielzeugkatalog"}], "topRanked": [{"contentId": "x9"}]}}}}}
        html = f'<html><a href="/">x</a><script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></html>'
        seen = []

        def fake(url, timeout=25, headers=None):
            if "Geschaefte" in url:
                return html
            if "/brochures/" in url:
                seen.append(url.split("/brochures/")[1].split("/")[0])
            return json.dumps({"searchResults": {}} if "/api/search" in url else {"contents": []})
        cfg = {**self.CFG, "publisher_pages": ["https://www.kaufda.de/Geschaefte/Smyths-Toys"], "max_brochures": 1}
        with mock.patch.object(fetch, "get", side_effect=fake):
            _, errors = flyers.check(cfg, P)
        self.assertEqual(errors, [])
        self.assertEqual(seen, ["s1"])

    def test_flyer_hit_in_run(self):
        cfg = {**CFG, "stores": None, "shopify_search": None, "shops": [], "flyers": self.CFG}
        state = main.load_state(Path("/nonexistent"))
        state["last_digest"] = "2026-09-29"
        with mock.patch.object(fetch, "get", side_effect=self.fake_get):
            subject, body = main.run(cfg, state, NOW, with_news=False)
            self.assertIn("🚨", subject)
            self.assertIn("Prospekt Leipzig", body)
            self.assertIsNone(main.run(cfg, state, NOW, with_news=False)[0], "nur einmal melden")


class MarktguruTests(unittest.TestCase):
    HOME = '<script type="application/json">{"config":{"apiKey":"A","clientKey":"C"}}</script>'
    RESULTS = {"results": [
        {"id": 7, "product": {"name": "Top-Trainer-Box 30 Jahre"}, "brand": {"name": "Pokémon"},
         "advertisers": [{"name": "HIT"}], "price": 59.99,
         "validityDates": [{"from": "2026-09-28T00:00:00Z", "to": "2026-10-03T23:59:59Z"}]},
        {"id": 8, "product": {"name": "Pils"}, "brand": {"name": "Krombacher"}, "price": 11.99}]}

    def test_marktguru(self):
        def get(url, timeout=25, headers=None):
            if "api.marktguru" in url:
                self.assertEqual(headers["x-apikey"], "A")
                return json.dumps(self.RESULTS)
            return self.HOME
        cfg = {"postal_code": "04275", "queries": ["Pokemon"], "related_any": []}
        with mock.patch.object(fetch, "get", side_effect=get):
            offers = flyers.marktguru_offers(cfg, P)
        self.assertEqual([(o["kind"], o["store"], o["price"], o["valid_until"]) for o in offers],
                         [("exact", "HIT", 59.99, "2026-10-03")])


class ShopifySearchTests(unittest.TestCase):
    def test_discover_new_listing(self):
        suggest = {"resources": {"results": {"products": [
            {"title": "Pokémon 30 Jahre Top-Trainer-Box (DE)", "url": "/products/ttb-30?_pos=1"},
            {"title": "Pokémon Top-Trainer-Box Karmesin", "url": "/products/ttb-kp"}]}}}

        def get(url, timeout=25, headers=None):
            if "neu.example" in url:
                return json.dumps(suggest)
            raise fetch.FetchError("HTTP 404")
        cfg = {"queries": ["30 Jahre"], "domains": ["neu.example", "kaputt.example", "known.example"]}
        state = {}
        with mock.patch.object(fetch, "get", side_effect=get):
            extra = shopify.discover(cfg, [{"urls": ["https://known.example/products/x"]}], P, state)
        self.assertEqual(extra, [{"name": "neu.example", "country": "DE", "ships_to_de": "yes",
                                  "urls": ["https://neu.example/products/ttb-30"], "discovered": True}])


class OcrTests(unittest.TestCase):
    def test_find_pokemon(self):
        hit = ocr.find_pokemon("Spielwaren\nPOKEMON Top-Trainer-Box\n30 Jahre 59,99 €\nLEGO", P)
        self.assertTrue(hit["exact"])
        self.assertIn(59.99, hit["prices"])
        self.assertFalse(ocr.find_pokemon("Pokémon Booster 4,99", P)["exact"])
        self.assertIsNone(ocr.find_pokemon("Krombacher 11,99", P))

    def test_page_images_bonial_policy(self):
        base = "https://content-media.bonial.biz/5006-86be/zoomlarge_page_0.jpg?impolicy="
        page = {"a": [{"url": base + p} for p in ("768x1024", "large", "preview", "zoomlarge")],
                "image": "https://content-media.bonial.biz/x/main.jpg"}
        self.assertEqual(ocr.page_images(page), base + "zoomlarge")

    def test_page_images_prefers_large(self):
        page = {"images": {"thumb": "https://img.example/thumb.jpg", "zoom": "https://img.example/page_1.jpg"},
                "link": "https://example/x.html"}
        self.assertEqual(ocr.page_images(page), "https://img.example/page_1.jpg")
        self.assertIsNone(ocr.page_images({"offers": []}))


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
