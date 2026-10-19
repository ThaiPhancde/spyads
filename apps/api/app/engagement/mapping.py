"""Ad ID → public Post ID / Video ID. Pure string work: a post is named only when the ad itself points at it.

  TikTok    https://www.tiktok.com/@user/video/<19-digit id>   (landing page / link / copy of the ad)
  Facebook  /<page>/posts/<id> · /<page>/videos/<id> · /reel/<id> · permalink.php?story_fbid=<id> · /watch/?v=<id> · fb.watch/<code>
Ad-library ads have none: a Meta Ad Library id (…/ads/library/?id=) and a TikTok Creative Center id are ad ids, not post ids, and
dark / boosted posts have no public URL. Those ads map to None and their engagement stays N/A — a post is never guessed from the
advertiser's page or a similar caption. Instagram / short links we cannot resolve offline return None too (reason says which).
"""
from __future__ import annotations

import re

TIKTOK = re.compile(r"(?:https?://)?(?:www\.|m\.)?tiktok\.com/@([\w.\-]{1,40})/video/(\d{15,22})", re.I)
TIKTOK_SHORT = re.compile(r"(?:https?://)?(?:vm|vt)\.tiktok\.com/\w+", re.I)
FB_POST = re.compile(r"(?:https?://)?(?:www\.|m\.|web\.)?facebook\.com/([\w.\-]+)/(posts|videos|reel|reels)/([\w\-]+)", re.I)
FB_REEL = re.compile(r"(?:https?://)?(?:www\.|m\.)?facebook\.com/(reel|reels)/(\d{6,})", re.I)
FB_PERMALINK = re.compile(r"(?:https?://)?(?:www\.|m\.)?facebook\.com/(?:permalink\.php|story\.php)\?[^\s\"']*story_fbid=(\d+)[^\s\"']*", re.I)
FB_WATCH = re.compile(r"(?:https?://)?(?:www\.|m\.)?facebook\.com/watch/?\?(?:[^\s\"']*&)?v=(\d+)", re.I)
FB_SHORT = re.compile(r"(?:https?://)?fb\.watch/[\w\-]+", re.I)
AD_LIBRARY = re.compile(r"facebook\.com/ads/library|creativecenter/topads", re.I)


def _fields(ad) -> list[tuple[str, str]]:
    return [(n, getattr(ad, n) or "") for n in ("landing_url", "snapshot_url", "ad_text", "title") if getattr(ad, n, None)]


def post_ref(ad) -> dict:
    """{platform, post_id, url, via} when the ad names a public post, else {"reason": ...} (never None: the reason is shown)."""
    for field, text in _fields(ad):
        if field == "snapshot_url" and AD_LIBRARY.search(text):
            continue  # the library page of the ad itself
        if m := TIKTOK.search(text):
            user, vid = m.group(1), m.group(2)
            return {"platform": "tiktok", "post_id": vid, "url": f"https://www.tiktok.com/@{user}/video/{vid}", "via": field}
        if m := FB_POST.search(text):
            page, kind, pid = m.groups()
            return {"platform": "facebook", "post_id": pid, "url": f"https://www.facebook.com/{page}/{kind.lower()}/{pid}", "via": field}
        if m := FB_REEL.search(text):
            return {"platform": "facebook", "post_id": m.group(2), "url": f"https://www.facebook.com/reel/{m.group(2)}", "via": field}
        if m := FB_PERMALINK.search(text):
            return {"platform": "facebook", "post_id": m.group(1), "url": m.group(0) if m.group(0).startswith("http") else f"https://{m.group(0)}", "via": field}
        if m := FB_WATCH.search(text):
            return {"platform": "facebook", "post_id": m.group(1), "url": f"https://www.facebook.com/watch/?v={m.group(1)}", "via": field}
        if m := FB_SHORT.search(text):  # Bright Data resolves it; the id comes back with the record
            return {"platform": "facebook", "post_id": None, "url": m.group(0) if m.group(0).startswith("http") else f"https://{m.group(0)}", "via": field}
        if TIKTOK_SHORT.search(text):
            return {"reason": "tiktok_short_link"}  # needs a redirect we do not follow for free
    src = (getattr(ad, "source", "") or "")
    if "tiktok" in src or (getattr(ad, "network", "") or "") == "tiktok":
        return {"reason": "tiktok_ad_id_is_not_a_video_id"}
    if src.startswith("meta") or (getattr(ad, "network", "") or "") == "meta":
        return {"reason": "ad_library_has_no_post_id"}
    return {"reason": "no_post_link"}
