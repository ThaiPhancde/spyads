"""Persist cluster membership without modifying any original Ad/Product or legacy history."""
from datetime import datetime

from sqlalchemy import select, text

from ..cluster_models import AdClusterMembership, ProductCluster
from ..models import Ad, Event, ProductLink

VERSION = "product-clustering-v1"


def lock(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(727021)"))


def root(nodes, pid):
    seen = set()
    while pid in nodes and nodes[pid].merged_into_id is not None:
        if pid in seen:
            raise ValueError("Cluster redirect cycle")
        seen.add(pid)
        pid = nodes[pid].merged_into_id
    return pid


def sync(db, ad_ids=None):
    """Cheap metadata assignment. No image decoding, model inference or provider calls."""
    lock(db)
    q = select(Ad.id, Ad.product_id).where(Ad.product_id.is_not(None))
    if ad_ids is not None:
        q = q.where(Ad.id.in_(ad_ids))
    ads = db.execute(q).all()
    nodes = {c.id: c for c in db.scalars(select(ProductCluster))}
    for pid in {a.product_id for a in ads} - nodes.keys():
        node = ProductCluster(id=pid)
        db.add(node)
        nodes[pid] = node
    db.flush()
    mq = select(AdClusterMembership)
    if ad_ids is not None:
        mq = mq.where(AdClusterMembership.ad_id.in_(ad_ids))
    have = {m.ad_id: m for m in db.scalars(mq)}
    for a in ads:
        m = have.get(a.id)
        rid = root(nodes, a.product_id)
        if m is None:
            db.add(AdClusterMembership(ad_id=a.id, source_product_id=a.product_id, cluster_id=rid,
                                      method="legacy" if rid == a.product_id else "inherited",
                                      evidence={"algorithm": VERSION}))
        elif m.source_product_id != a.product_id:
            # A legacy manual split changed product ownership. Preserve the old assignment in its evidence.
            m.evidence = {"algorithm": VERSION, "previous_cluster_id": m.cluster_id,
                          "previous_product_id": m.source_product_id}
            m.source_product_id, m.cluster_id = a.product_id, rid
            m.method, m.confidence, m.updated_at = "reassigned", None, datetime.utcnow()
    db.flush()
    return nodes


def join(db, source_pid, target_pid, confidence, evidence, by, st, manual=False):
    """Validate the WHOLE two clusters, including cannot-links, before any union (prevents A-B-C bridges)."""
    from . import clustering as C

    lock(db)
    nodes = {c.id: c for c in db.scalars(select(ProductCluster))}
    src, dst = root(nodes, source_pid), root(nodes, target_pid)
    if src == dst:
        return dst, None
    left = {pid for pid in nodes if root(nodes, pid) == src}
    right = {pid for pid in nodes if root(nodes, pid) == dst}
    for x in left:
        for y in right:
            px, py = st.prods.get(x), st.prods.get(y)
            if not px or not py:
                raise ValueError("missing_product")
            sig = C._sig(st, x, y).copy()
            sig["name"] = C.title_similarity(px["name"], py["name"])
            hard, soft = C.guards(sig, px, py, st.rejected)
            if hard or (soft and not manual):
                raise ValueError(",".join(hard or soft))
    members = list(db.scalars(select(AdClusterMembership).where(AdClusterMembership.cluster_id == src)))
    total = len(members) + sum(p["n_ads"] for pid, p in st.prods.items() if pid in right)
    if not manual and total > C.MAX_MERGED_ADS:
        raise ValueError("big_cluster")
    prior = [{"ad_id": m.ad_id, "confidence": m.confidence, "method": m.method, "evidence": m.evidence}
             for m in members]
    nodes[src].merged_into_id = dst
    nodes[src].revision += 1
    nodes[dst].revision += 1
    for m in members:
        m.cluster_id = dst
        m.confidence = min(m.confidence, confidence) if m.confidence is not None else confidence
        m.method = "manual" if manual else "automatic"
        m.evidence = {"algorithm": VERSION, "signals": evidence, "source_cluster_id": src}
        m.updated_at = datetime.utcnow()
    event = Event(type="PRODUCT_CLUSTERED", product_id=dst, data={
        "source_cluster_id": src, "target_cluster_id": dst, "source_products": sorted(left),
        "target_products": sorted(right), "previous_memberships": prior,
        "source_revision": nodes[src].revision, "target_revision": nodes[dst].revision,
        "pair": [source_pid, target_pid], "confidence": confidence, "by": by, "algorithm": VERSION})
    db.add(event)
    db.flush()
    return dst, event.id


def undo(db, event, by):
    from .clustering import record_cannot_link

    lock(db)
    d = event.data or {}
    src, dst = db.get(ProductCluster, d["source_cluster_id"]), db.get(ProductCluster, d["target_cluster_id"])
    if d.get("undone") or not src or not dst or src.merged_into_id != dst.id or dst.merged_into_id is not None:
        raise ValueError("Cụm đã thay đổi; hoàn tác thao tác mới nhất trước")
    if src.revision != d["source_revision"] or dst.revision != d["target_revision"]:
        raise ValueError("Cụm đã thay đổi; hoàn tác thao tác mới nhất trước")
    previous = {m["ad_id"]: m for m in d["previous_memberships"]}
    members = db.scalars(select(AdClusterMembership).where(
        AdClusterMembership.cluster_id == dst.id,
        AdClusterMembership.source_product_id.in_(d["source_products"]))).all()
    for m in members:
        old = previous.get(m.ad_id)
        m.cluster_id = src.id
        m.confidence, m.method, m.evidence = ((old["confidence"], old["method"], old["evidence"])
                                            if old else (None, "inherited", {"algorithm": VERSION}))
        m.updated_at = datetime.utcnow()
    src.merged_into_id = None
    src.revision -= 1
    dst.revision -= 1
    # Do not let a different pair bridge the same two groups back together.
    for x in d["source_products"]:
        for y in d["target_products"]:
            record_cannot_link(db, x, y, by)
    event.data = {**d, "undone": True, "undone_by": by}
    for link in db.scalars(select(ProductLink).where(ProductLink.event_id == event.id)):
        link.status, link.decided_by, link.decided_at = "rejected", by, datetime.utcnow()
    db.flush()
    return {src.id, dst.id}
