"""Real Chromium IndexedDB persistence; network delivery remains a controlled stub."""
from pathlib import Path
import pytest


def test_indexeddb_survives_browser_restart(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    source = (Path(__file__).resolve().parents[2] / "extension" / "outbox.js").read_text(encoding="utf-8")
    with pw.sync_playwright() as p:
        def start():
            context = p.chromium.launch_persistent_context(str(tmp_path / "profile"), headless=True)
            context.route("http://outbox.test/**", lambda route: route.fulfill(content_type="text/html", body="<html></html>"))
            page = context.new_page(); page.goto("http://outbox.test/")
            page.add_script_tag(content=source)
            return context, page
        context, page = start()
        page.evaluate("async () => { await ToolSpyOutbox.store.enqueue({id:'persist', nextAt:0}); await ToolSpyOutbox.drain(ToolSpyOutbox.store, async () => {throw Error('offline')}, () => 0); }")
        assert page.evaluate("async () => (await ToolSpyOutbox.store.all())[0].attempts") == 1
        # A repeated content-script handoff must not reset retries or replace the stored payload.
        page.evaluate("async () => ToolSpyOutbox.store.enqueue({id:'persist', nextAt:0, attempts:99})")
        assert page.evaluate("async () => (await ToolSpyOutbox.store.all())[0].attempts") == 1
        context.close()
        context, page = start()
        assert page.evaluate("async () => (await ToolSpyOutbox.store.all())[0].id") == "persist"
        page.evaluate("async () => ToolSpyOutbox.drain(ToolSpyOutbox.store, async () => {}, () => 999999)")
        assert page.evaluate("async () => (await ToolSpyOutbox.store.all()).length") == 0
        context.close()
