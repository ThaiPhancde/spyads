"""Allegro: registry entry only. Data arrives by file import (..allegro_import, apps/api/data/imports/allegro.json); this
connector never touches the network (no search / fetch capability, only "import")."""
from __future__ import annotations

from .base import BaseConnector


class AllegroImportConnector(BaseConnector):
    source, label, kind = "allegro", "Allegro (import JSON)", "marketplace"
    countries = ("PL",)
    capabilities = frozenset({"import"})
    docs_url = "https://allegro.pl/"
    notes = ("Tìm qua Grok Bot (trình duyệt, webhook + callback) hoặc nhập file apps/api/data/imports/allegro.json. "
             "Crawler HTTP tạm tắt (DataDome 403).")

    def configured(self) -> bool:
        return True

    async def healthcheck(self) -> dict:
        from .. import allegro_import

        s = allegro_import.status()
        return {"ok": s["state"] == "file_found", "note": s.get("message") or f"{s.get('rows', 0)} dòng trong file"}
