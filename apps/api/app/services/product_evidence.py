"""Evidence within the existing product cluster. No network calls or CLIP inference.

Use cached hashes/embeddings to count creative concepts conservatively. URL, copy,
collation_count and a different file hash alone are not proof of a new concept.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import urlparse

from ..platforms import REMOVED_NETWORKS
from .entity_resolution import TRIVIAL_PHASH


def external_ads(ads):
    return [a for a in ads if a.channel in (None, "ads") and not a.is_internal and a.network not in REMOVED_NETWORKS]


def ad_key(a):
    return f"{a.network or a.platform}:{a.external_id}" if a.external_id else f"row:{a.id}"


def advertiser_groups(ads):
    """Join page identities through cluster advertiser IDs; missing identity never counts as a seller."""
    pages = defaultdict(set)
    for a in ads:
        if a.advertiser_id and a.page_id:
            pages[a.advertiser_id].add((a.network or a.platform, a.page_id))
    groups, missing = set(), False
    for a in ads:
        linked = pages.get(a.advertiser_id, set())
        if a.page_id:
            groups.add((a.network or a.platform, a.page_id))
        elif len(linked) == 1:
            groups.add(next(iter(linked)))
        elif a.advertiser_id and not linked:
            groups.add(("advertiser", a.advertiser_id))
        else:
            missing = True
    return groups, missing


def capture_activity(ads, now=None):
    """Only today's source-observed state; never synthesise yesterday from today's rows."""
    now = now or datetime.utcnow()
    ads = external_ads(ads)
    known, active, units, markets = set(), set(), {}, {}
    for a in ads:
        key = ad_key(a)
        known.add(key)
        # A stale crawl is not a recent verification of running/stopped state.
        seen = max((x for x in (a.last_verified_at, a.last_seen_at) if x), default=None)
        if seen is None or seen < now - timedelta(days=2):
            continue
        if a.is_active:
            active.add(key)
            units[key] = max(units.get(key, 0), a.variants or 1)
        markets[key] = sorted(set(a.countries or []) | ({a.country} if a.country else set()))
    return {"version": 1, "captured_at": now.isoformat(), "known": sorted(known),
            "observed": sorted(markets), "active": sorted(active), "units": units, "markets": markets}


def activity_momentum(snapshots, ads, now=None):
    """Sustained growth in a fixed, observed cohort over >=7 days, excluding newly crawled IDs."""
    now = now or datetime.utcnow()
    keys = {ad_key(a) for a in external_ads(ads)}
    history = sorted([s for s in snapshots if s.activity_evidence and s.activity_evidence.get("version") == 1
                      and now.date() - timedelta(days=14) <= s.date <= now.date()], key=lambda s: s.date)
    # One daily observation, even if multiple snapshot jobs wrote the same date.
    history = list({s.date: s for s in history}.values())
    if len(history) < 3 or (history[-1].date - history[0].date).days < 7 or (now.date() - history[-1].date).days > 2:
        return {"confirmed": False, "available": False, "reason": "Thiếu ít nhất 3 snapshot quan sát trong 7 ngày; lịch sử dựng lại không được tính"}
    baseline, recent = history[0], history[-2:]
    # Snapshot date and its capture time must agree; a historical backfill cannot fabricate observations.
    for s in history:
        try:
            captured = datetime.fromisoformat(s.activity_evidence["captured_at"])
        except (KeyError, ValueError, TypeError):
            return {"confirmed": False, "available": False, "reason": "Snapshot thiếu thời điểm quan sát có thể kiểm chứng"}
        if captured.date() != s.date:
            return {"confirmed": False, "available": False, "reason": "Snapshot lịch sử dựng lại không xác nhận momentum"}
    cohort = keys & set(baseline.activity_evidence["known"])
    cohort &= set(baseline.activity_evidence["observed"])
    for s in recent:
        cohort &= set(s.activity_evidence["observed"])
    if not cohort:
        return {"confirmed": False, "available": False, "reason": "Chưa có cùng tập quảng cáo được quan sát ở cả đầu và cuối kỳ"}
    def counts(s):
        e = s.activity_evidence
        active = cohort & set(e["active"])
        return len(active), sum(e.get("units", {}).get(k, 1) for k in active)
    before_ads, before_units = counts(baseline)
    after = [counts(s) for s in recent]
    increased = lambda b, n: n - b >= 1 and (n - b) / max(1, b) >= .2
    confirmed = all(increased(before_ads, n) for n, _ in after) or all(increased(before_units, n) for _, n in after)
    return {"confirmed": confirmed, "available": True, "baseline_date": str(baseline.date),
            "latest_date": str(history[-1].date), "cohort": len(cohort), "active_before": before_ads,
            "active_after": after[-1][0], "units_before": before_units, "units_after": after[-1][1],
            "reason": ("Hoạt động quảng cáo tăng ≥20%, duy trì ở 2 snapshot cuối trên cùng tập đã biết" if confirmed
                       else "Snapshot chưa xác nhận tăng trưởng duy trì trên cùng tập quảng cáo")}


def creative_replication(ads, creatives, images=None):
    """At least two distinct visual concepts on different active ads, in the same product cluster."""
    active = {a.id: a for a in external_ads(ads) if a.is_active}
    images = images or {}
    candidates, parent, hash_nodes, metadata_nodes, node_ads = [], [], {}, {}, []
    evidence_count = 0
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for c in creatives:
        if c.ad_id not in active:
            continue
        image = images.get(c.id)
        phash = (image.phash if image else None) or c.phash
        if not phash or phash in TRIVIAL_PHASH:
            continue
        try:
            hash_int = int(phash, 16)
        except ValueError:
            continue
        a = active[c.ad_id]
        evidence_count += 1
        sha = (image.image_sha256 if image else None) or c.sha256
        u = urlparse(c.source_url or "")
        asset = (u.hostname, u.path) if c.source_url else None
        # Exact hashes and metadata collapse before pairwise visual work. All assets still contribute
        # their ad identity and metadata edges; large clusters are never truncated.
        i = hash_nodes.get(hash_int)
        if i is None:
            i = len(candidates)
            vector, model = None, None
            if image and image.embedding:
                import numpy as np
                vector = np.frombuffer(image.embedding, dtype=np.float16).astype(np.float32)
                norm = float(np.linalg.norm(vector))
                vector = vector / norm if norm > 0 else None
                model = image.emb_model
            candidates.append((sha, hash_int, asset, c.family_id, vector, model))
            parent.append(i)
            node_ads.append(set())
            hash_nodes[hash_int] = i
        node_ads[i].add(ad_key(a))
        for kind, value in (("sha", sha), ("asset", asset), ("family", c.family_id)):
            if value:
                key = (kind, value)
                if key in metadata_nodes:
                    parent[root(i)] = root(metadata_nodes[key])
                else:
                    metadata_nodes[key] = i
    def same(x, y):
        sha, h, asset, family, v, model = x
        other, j, other_asset, other_family, w, other_model = y
        if sha and sha == other or (h ^ j).bit_count() <= 6:
            return True
        # Same asset URL / family + metadata protects against crops and transcoding.
        if asset and asset == other_asset:
            return True
        if family and family == other_family:
            return True
        if v is not None and w is not None and model == other_model:
            if v.shape == w.shape and v.size and float(v @ w) >= .97:
                return True
        return False
    # Only unique perceptual hashes are compared; duplicates do not repeat URL parsing or CLIP decoding.
    for i, x in enumerate(candidates):
        for j in range(i):
            if root(i) == root(j):
                continue
            if same(x, candidates[j]):
                parent[root(i)] = root(j)
    groups = defaultdict(set)
    for i in range(len(candidates)):
        groups[root(i)].update(node_ads[i])
    # A carousel's two pictures on one ad are not two replicated ads.
    confirmed = any(x != y for i, a in enumerate(groups.values()) for b in list(groups.values())[i + 1:] for x in a for y in b)
    return {"confirmed": confirmed, "distinct_creatives": len(groups), "visual_evidence": evidence_count,
            "reason": (f"{len(groups)} creative khác biệt trên các quảng cáo độc lập, đã loại trùng hash/CLIP và metadata" if confirmed
                       else "Chưa có 2 creative khác biệt được xác nhận trên các quảng cáo độc lập")}
