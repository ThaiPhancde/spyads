"""TikTok Commercial Content API mapper self-check (offline): .venv/Scripts/python test_tiktok_commercial.py"""
from app.collector.adapters.generic import TikTokCommercialConnector, _tiktok_count, map_tiktok_commercial
from app.collector.base import FetchParams

assert [_tiktok_count(v) for v in ("11K", "1.2M", "100K+", "850", None)] == [11000, 1200000, 100000, 850, None]
it = {"ad": {"id": 1923845247192304, "first_shown_date": "20260901", "last_shown_date": "20261005", "status": "active",
             "videos": [{"url": "https://v.tiktokcdn.com/a.mp4", "cover_image_url": "https://p/c.jpg"}], "reach": {"unique_users_seen": "11K"}},
      "advertiser": {"business_id": 1755645247067185, "business_name": "Awe Co."}}
det = {"ad": {"external_url": "https://shop.example/p", "call_to_action": "Shop now", "title": "Lucky bracelet"},
       "advertiser": {"profile_url": "https://tiktok.com/@awe", "follower_count": 1200},
       "ad_group": {"targeting_info": {"country": ["fr"]}}}
r = map_tiktok_commercial(it, det, ["FR", "DE"], "lucky bracelet")
assert (r.source_ad_id, r.first_seen, r.active, r.reach, r.impressions_text) == ("1923845247192304", "2026-09-01", True, 11000, "11K")
assert (r.landing_page, r.cta_text, r.country, r.countries) == ("https://shop.example/p", "Shop now", "FR", ["FR"])
assert r.media[0].type == "video" and r.media[0].preview_url == "https://p/c.jpg"
assert map_tiktok_commercial(it, {}, ["FR", "DE"], None).countries == ["FR", "DE"]  # no detail → requested countries

# PH-only search must not hit the API at all (coverage is EU/UK only; no fallback that leaks EU ads into PH)
c = TikTokCommercialConnector({"client_key": "k", "client_secret": "s"})
c.query = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not query"))
assert list(c.fetch_ads(FetchParams(query="bracelet", countries=["PH"]))) == []

# pagination: follows search_id, stops at has_more=False, dedupes
pages = iter([{"ads": [it], "has_more": True, "search_id": "s1"}, {"ads": [it, {**it, "ad": {**it["ad"], "id": 2}}], "has_more": False}])
c.query = lambda *a, **k: next(pages)
c._post = lambda *a, **k: {}
assert [x.source_ad_id for x in c.fetch_ads(FetchParams(query="bracelet", countries=["FR", "PH"]))] == ["1923845247192304", "2"]
print("ok")
