import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Connector
from app.connector_identity import identity
from app.platforms import connector_network, network_of
from app.services.connectors import ensure_default_connectors
from app.collector.adapters import generic
from app.collector.base import FetchParams, NotConfigured, ConnectorError
from app.routers.intel import CollectorIn, CollectorPatch, add_connector, patch_connector
from fastapi import HTTPException


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(generic, "DATA_DIR", tmp_path / "data")
    engine = create_engine(f"sqlite:///{tmp_path}/connectors.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_provider_is_not_network_or_adapter():
    pipi, minea = identity("export", {"source": "Pipiads"}), identity("export", {"source": "minea"})
    assert pipi["label"] == "Pipiads" and minea["label"] == "Minea"
    assert pipi["key"] != minea["key"] and pipi["color"] != minea["color"]
    assert connector_network("export", {"source": "pipiads"}) == "other"
    assert connector_network("export", {"source": "minea", "platform": "tiktok"}) == "tiktok"
    assert network_of("facebook", "pipiads") == "meta"  # actual placement wins


def test_rename_is_idempotent_preserves_existing_config_and_switch(db):
    old = Connector(name="Export · pipiads", provider="export", adapter="export", kind="export", group="ad_intel",
                    enabled=False, config={"source": "pipiads", "country": "PH", "mapping": {"title": "Title"}}, every_minutes=17)
    db.add(old)
    db.commit()
    cid = old.id
    ensure_default_connectors(db)
    db.commit()
    ensure_default_connectors(db)
    db.commit()
    matching = [c for c in db.scalars(select(Connector).where(Connector.adapter == "export")) if c.config.get("source") == "pipiads"]
    assert len(matching) == 1 and matching[0].id == cid
    assert matching[0].provider == "pipiads" and matching[0].name.startswith("Pipiads ·")
    assert not matching[0].enabled and matching[0].every_minutes == 17 and matching[0].config["country"] == "PH"
    assert matching[0].health == "waiting_file"


def test_mixed_network_export_preserves_source_and_reads_canonical_ids():
    rows = [{"source_ad_id": "p1", "platform": "Facebook", "Ad Title": "Neck massager"},
            {"source_ad_id": "p2", "Ad Platform": "TikTok", "title": "Neck massager"}]
    records = [generic.row_to_record(r, "pipiads") for r in rows]
    assert [r.source_ad_id for r in records] == ["p1", "p2"]
    assert records[0].title == "Neck massager"
    assert [network_of(r.platform, r.source) for r in records] == ["meta", "tiktok"]
    assert all(r.source == "pipiads" for r in records)
    assert generic.row_to_record({"URL": "https://store.test/", "Title": "Store list"}, "minea") is None
    assert generic.row_to_record({"Ad ID": "m1"}, "minea", default_platform="tiktok").platform == "tiktok"
    with pytest.raises(ConnectorError, match="Platform"):
        generic.row_to_record({"Ad ID": "unknown-platform"}, "pipiads")


def test_folders_are_isolated_and_health_does_not_claim_live_access(db):
    pipi = generic.ExportFolderConnector({"source": "pipiads"})
    minea = generic.ExportFolderConnector({"source": "minea"})
    assert pipi.health_check()["status"] == minea.health_check()["status"] == "waiting_file"
    pipi.folder().mkdir(parents=True)
    (pipi.folder() / "ads.csv").write_text('Ad ID,Title,Platform\np1,Neck massager,Facebook\n', encoding="utf-8")
    assert pipi.health_check()["pending_files"] == 1
    assert minea.health_check()["pending_files"] == 0
    records = list(pipi.fetch_ads(FetchParams()))
    assert len(records) == 1 and records[0].source == "pipiads" and records[0].platform == "facebook"
    assert list(minea.fetch_ads(FetchParams())) == []
    assert list(pipi.fetch_ads(FetchParams())) == []  # archived, no repeat ingest
    with pytest.raises(NotConfigured):
        generic.ExportFolderConnector({"source": "../minea"}).folder()


def test_bad_export_is_kept_for_fixing(db):
    c = generic.ExportFolderConnector({"source": "minea"})
    c.folder().mkdir(parents=True)
    file = c.folder() / "bad.csv"
    file.write_text('Wrong column\nsomething\n', encoding="utf-8")
    with pytest.raises(ConnectorError, match="mapping"):
        list(c.fetch_ads(FetchParams()))
    assert file.exists()


def test_concurrent_watchers_do_not_read_the_same_file_twice(db):
    from concurrent.futures import ThreadPoolExecutor
    c = generic.ExportFolderConnector({"source": "minea"})
    c.folder().mkdir(parents=True)
    (c.folder() / "ads.csv").write_text('Ad ID,Title,Platform\nm1,Neck massager,TikTok\n', encoding="utf-8")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: list(c.fetch_ads(FetchParams())), range(2)))
    assert sum(len(r) for r in results) == 1


def test_duplicate_watchers_and_reassignments_are_rejected(db):
    pipi = add_connector(CollectorIn(adapter="export", config={"source": "Pipiads"}), db)
    minea = add_connector(CollectorIn(adapter="export", config={"source": "minea"}), db)
    with pytest.raises(HTTPException) as err:
        add_connector(CollectorIn(adapter="export", config={"source": "pipiads"}), db)
    assert err.value.status_code == 409
    with pytest.raises(HTTPException) as err:
        patch_connector(minea["id"], CollectorPatch(config={"source": "pipiads"}), db)
    assert err.value.status_code == 409
    assert db.get(Connector, pipi["id"]).provider == "pipiads"


def test_http_template_reports_missing_configuration_instead_of_healthy():
    with pytest.raises(NotConfigured, match="url"):
        generic.GenericHttpConnector({"source": "pipiads"}).health_check()
    configured = generic.GenericHttpConnector({"source": "pipiads", "url": "https://api.example.test/ads", "mapping": {"source_ad_id": "id"}})
    assert configured.health_check()["status"] == "configured"
    other = generic.GenericHttpConnector({"source": "minea"})
    assert configured.bucket is not other.bucket


def test_export_records_reach_ingest_with_separate_sources_and_networks(db, monkeypatch):
    from app import ingest, media
    from app.models import Ad, AdSource
    from app.services import entity_resolution
    monkeypatch.setattr(ingest, "write_raw_batch", lambda *a: "test/raw.json")
    monkeypatch.setattr(media, "enqueue", lambda *a: None)
    monkeypatch.setattr(ingest.E, "extract_products", lambda *a: None)
    entity_resolution._idx.update(pid=0, aid=0, names={}, inv={})
    records = [generic.row_to_record({"Ad ID": "42", "Title": "Neck massager", "Platform": platform,
               "Country": "PH", "Advertiser": f"Shop {source}"}, source)
               for source, platform in (("pipiads", "Facebook"), ("minea", "TikTok"))]
    result = ingest.ingest_ads(db, records)
    assert result["new_ads"] == 2 and not result["errors"]
    assert {(a.source, a.network) for a in db.scalars(select(Ad))} == {("pipiads", "meta"), ("minea", "tiktok")}
    assert {a.source for a in db.scalars(select(AdSource))} == {"pipiads", "minea"}
    repeated = ingest.ingest_ads(db, records)
    assert repeated["new_ads"] == 0 and repeated["updated_ads"] == 2


def test_failed_run_cannot_show_count_from_previous_success(db, monkeypatch):
    from app import realtime
    from app.collector.factory import ConnectorFactory
    from sqlalchemy.orm import sessionmaker
    from types import SimpleNamespace
    c = Connector(name="1688 test", adapter="ali1688", provider="ali1688", kind="official_api", group="marketplace", config={}, last_sync_count=42)
    db.add(c)
    db.commit()
    def fail(params):
        raise ConnectorError("bad_response")
    monkeypatch.setattr(realtime, "SessionLocal", sessionmaker(db.bind))
    monkeypatch.setattr(ConnectorFactory, "get", lambda *a: SimpleNamespace(fetch_ads=fail))
    realtime.run_connector(c.id, params=FetchParams(query="x"))
    db.expire_all()
    assert db.get(Connector, c.id).health == "error" and db.get(Connector, c.id).last_sync_count == 0
