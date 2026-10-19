"""Backfill verdicts and start real activity evidence. Offline: no provider or model calls.
Run from apps/api: .venv/Scripts/python scripts/backfill_hidden_v3.py --apply
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from app import models
from app.db import SessionLocal, auto_migrate, read_only
from app.models import Product, Ad, ProductDailySnapshot, MarketDailySnapshot
from app.pricing.models import PriceObservation, NOT_FOUND
from app.services import hidden
from app.services.product_evidence import capture_activity, advertiser_groups, external_ads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        auto_migrate()
    root = Path(__file__).resolve().parents[3]
    before_path = root / "docs/hidden_winners_before.json"
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.exists() else {}
    with SessionLocal() as db, read_only():
        rows = hidden.compute(db, include_unconfirmed=True)
        global_rows = hidden.compute(db, scope="ANY", include_unconfirmed=True)
        products = list(db.scalars(select(Product)))
        by_id = {r["product"].id: r for r in global_rows}
        all_ads = defaultdict(list)
        for a in db.scalars(select(Ad).where(Ad.product_id.is_not(None))):
            all_ads[a.product_id].append(a)
        by_status = Counter(r["status"] for r in rows)
        confirmed = [r for r in rows if r["status"] == "CONFIRMED"]
        old_ids = set(before.get("ids_before", []))
        report = {"rule_version": hidden.RULE_VERSION, "at": datetime.utcnow().isoformat(), "applied": args.apply,
                  "before": before.get("hidden_before"), "after": len(confirmed), "status_counts": dict(by_status),
                  "old_candidates_after": dict(Counter(r["status"] for r in rows if r["product"].id in old_ids)),
                  "confirmed_by_evidence": dict(Counter(t for r in confirmed for t in r["tags"] if t in ("momentum", "replication"))),
                  "confirmed_ids": [r["product"].id for r in confirmed],
                  "snapshot_note": "Old reconstructed snapshots are not promoted to momentum evidence; real observation starts today.",
                  "opportunity_unchanged": True}
    if args.apply:
        now = datetime.utcnow()
        for offset in range(0, len(products), 100):
            with SessionLocal() as db:
                for old in products[offset:offset+100]:
                    p = db.get(Product, old.id)
                    if not p:
                        continue
                    row = by_id.get(p.id) or hidden.assess(p, [], now=now)
                    row["product"] = p
                    current_opportunity = p.opportunity_score
                    hidden.store_verdict(p, row)
                    assert p.opportunity_score == current_opportunity  # concurrent scoring is not a backfill change
                    ads = all_ads[p.id]
                    if not ads:
                        continue
                    snap = db.scalar(select(ProductDailySnapshot).where(ProductDailySnapshot.product_id == p.id, ProductDailySnapshot.date == now.date()).order_by(ProductDailySnapshot.id.desc()))
                    if snap is None:
                        snap = ProductDailySnapshot(product_id=p.id, date=now.date(),
                              active_ads=sum(bool(a.is_active) for a in external_ads(ads)), advertisers=len(advertiser_groups(external_ads(ads))[0]),
                              opportunity_score=p.opportunity_score)
                        db.add(snap)
                    # Does not rewrite history. Capture only the actual current observation.
                    snap.activity_evidence = capture_activity(ads, now)
                db.commit()
        # Today's rollup must agree with the current labels; historical daily counts stay historical.
        with SessionLocal() as db:
            for snap in db.scalars(select(MarketDailySnapshot).where(MarketDailySnapshot.date == now.date())):
                snap.hidden_winners = sum(1 for r in confirmed if r["product"].category == snap.category and snap.country in r["countries"])
            # Old negative cache entries should not hide the new free sourcing path.
            for obs in db.scalars(select(PriceObservation).where(PriceObservation.price_type == NOT_FOUND, PriceObservation.expires_at > now)):
                if (obs.evidence or {}).get("rule_version") != 2:
                    obs.expires_at = now
            db.commit()
    target = root / "docs/hidden_winners_v3_comparison.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "confirmed_ids"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
