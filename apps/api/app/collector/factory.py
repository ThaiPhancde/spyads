"""Connector Factory (spy_app_chat_summary §10). Adding a source = add one class here."""
from __future__ import annotations

from .adapters.ad_libraries import (SnapchatAdsLibraryConnector, TikTokAdLibraryConnector, TikTokTopAdsConnector,
                                    TikTokTopAdsHeadlessConnector)
from .adapters.ali1688 import Ali1688Connector
from .adapters.apify import (Apify1688Connector, ApifyTaobaoConnector,
                             ApifyTikTokLibraryConnector, ApifyTikTokTopAdsConnector)
from .adapters.generic import (ApifyActorConnector, ExportFolderConnector, GenericHttpConnector,
                               TikTokCommercialConnector)
from .adapters.marketplaces import AliExpressSearchConnector
from .adapters.meta import ApifyMetaConnector, MetaGraphConnector, MetaLibraryConnector
from .base import BaseConnector


class ConnectorFactory:
    connectors: dict[str, type[BaseConnector]] = {
        c.key: c for c in (
            MetaLibraryConnector, MetaGraphConnector, ApifyMetaConnector,
            TikTokCommercialConnector, ApifyActorConnector, GenericHttpConnector, ExportFolderConnector,
            SnapchatAdsLibraryConnector, TikTokAdLibraryConnector, TikTokTopAdsConnector,
            TikTokTopAdsHeadlessConnector, AliExpressSearchConnector, Ali1688Connector, Apify1688Connector, ApifyTaobaoConnector, ApifyTikTokTopAdsConnector,
            ApifyTikTokLibraryConnector,
        )
    }

    @classmethod
    def get(cls, adapter: str, config: dict | None = None) -> BaseConnector:
        if adapter not in cls.connectors:
            raise KeyError(f"unknown connector adapter '{adapter}'")
        return cls.connectors[adapter](config or {})

    @classmethod
    def catalogue(cls) -> list[dict]:
        return [{"adapter": k, "name": c.name, "kind": c.kind, "group": c.group, "supports_search": c.supports_search,
                 "config_fields": c.config_fields,
                 "rate_limit": {"requests_per_minute": c.rate_limit.requests_per_minute, "retries": c.rate_limit.retries}}
                for k, c in cls.connectors.items()]


# Push-only sources (no fetch): data arrives through POST /ingest/*
PUSH_SOURCES = {
    "extension": "Chrome extension (MKT Save to Intelligence)",
}
