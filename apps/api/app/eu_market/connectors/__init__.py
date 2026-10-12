"""Source registry (spec §3 / image-search spec §6.2). Status vocabulary per source:
  available              implemented against a verified spec (state on Data Sources: ready | missing_credentials | last run code)
  access_restricted      the only API is a seller / partner API (needs a seller account, EU business licence or a paid plan)
  unsupported_capability no API / feed verified for competitor product search — never scraped
Public-data-first (§6.1): `jsonld` (any public product page) needs no seller account;
Kaufland / bol / BigBuy stay available but *optional* (seller / paid credentials)."""
from __future__ import annotations

from .base import BaseConnector, ConnectorError
from .allegro import AllegroImportConnector
from .bigbuy import BigBuyConnector
from .bol import BolConnector
from .jsonld import JsonLdConnector
from .kaufland import KauflandConnector

ACCESS_RESTRICTED, UNSUPPORTED_CAPABILITY = "access_restricted", "unsupported_capability"


def _planned(source, label, kind, countries, env_vars, docs_url, notes, status=ACCESS_RESTRICTED):
    return type(f"{source.title()}Planned", (BaseConnector,), {
        "source": source, "label": label, "kind": kind, "status": status, "countries": tuple(countries),
        "env_vars": tuple(env_vars), "docs_url": docs_url, "notes": notes})


PLANNED = [
    _planned("emag", "eMAG", "marketplace", ["RO", "HU", "BG"], ["EMAG_USERNAME", "EMAG_PASSWORD"],
             "https://marketplace.emag.ro/", "Seller API = own catalog/offers; no public competitor search."),
    _planned("otto", "OTTO", "marketplace", ["DE"], [], "https://api.otto.market/docs", "Partner API for integrations — not competitor intelligence."),
    _planned("cdiscount", "Cdiscount", "marketplace", ["FR"], [], "https://dev.cdiscount.com/", "Seller API — seller credentials, own catalog. Public pages: try the JSON-LD connector."),
    _planned("manomano", "ManoMano", "marketplace", ["FR", "DE", "IT", "ES", "GB"], [], "https://www.manomano.com/", "Partner integration (home/DIY) — scope to verify."),
    _planned("zalando", "Zalando", "marketplace", ["DE", "FR", "IT", "ES", "NL", "PL", "SE"], [], "https://developers.merchants.zalando.com/", "Fashion partner API — limited for competitor analysis."),
    _planned("onbuy", "OnBuy", "marketplace", ["GB"], [], "https://www.onbuy.com/", "API scope to verify; no public competitor API assumed."),
    _planned("faire", "Faire", "supplier", ["EU"], [], "https://www.faire.com/", "B2B wholesale; partner integrations only — no open catalog API assumed."),
    _planned("ankorstore", "Ankorstore", "supplier", ["EU"], [], "https://www.ankorstore.com/", "B2B wholesale; partner integration, may need retailer verification."),
    _planned("orderchamp", "Orderchamp", "supplier", ["NL", "BE", "DE"], [], "https://www.orderchamp.com/", "B2B lifestyle; API/permission to verify."),
    _planned("merkandi", "Merkandi", "supplier", ["EU"], [], "https://merkandi.com/", "Clearance stock; no public catalog API verified — not scraped.", UNSUPPORTED_CAPABILITY),
    _planned("europages", "Europages / wlw", "supplier", ["EU"], [], "https://www.europages.co.uk/", "Supplier directory (leads, not wholesale prices); no API verified.", UNSUPPORTED_CAPABILITY),
    _planned("solostocks", "SoloStocks", "supplier", ["ES"], [], "https://www.solostocks.com/", "Wholesale ES; contract/API to verify.", UNSUPPORTED_CAPABILITY),
]

# order = how Data Sources lists them: public-data-first, then optional seller connectors, then the rest
CONNECTORS: dict[str, type[BaseConnector]] = {c.source: c for c in (JsonLdConnector, AllegroImportConnector, KauflandConnector, BolConnector, BigBuyConnector, *PLANNED)}
OPTIONAL_SELLER = ("kaufland", "bol", "bigbuy")  # still supported, never required (spec §6.1)


def get(source: str) -> BaseConnector:
    if source not in CONNECTORS:
        raise ConnectorError("unsupported_capability", f"unknown source {source}")
    return CONNECTORS[source]()  # fresh instance per run: its semaphore binds to the running event loop


def catalogue() -> list[dict]:
    return [cls().info() for cls in CONNECTORS.values()]


def searchable(country: str | None = None) -> list[str]:
    """Configured sources that can search (optionally in `country`)."""
    out = []
    for s, cls in CONNECTORS.items():
        c = cls()
        if c.status == "available" and "search" in c.capabilities and c.configured() and (not country or country in c.countries):
            out.append(s)
    return out
