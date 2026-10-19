"""Thumbnails for products whose cards are empty: TikTok creatives are re-asked by ad id (media.refresh_source) and the
cover saved (media.grab_thumb). Meta signed links cannot be re-asked — those heal when the ad is collected again
(ingest._attach_creatives queues a creative without thumbnail). python scripts/refresh_thumbs.py [limit]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from app import media  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Creative  # noqa: E402

lim = int(sys.argv[1]) if len(sys.argv) > 1 else 200
with SessionLocal() as db:
    ids = [r[0] for r in db.execute(text("""select distinct on (c.product_id) c.id from creatives c join products p on p.id=c.product_id
        where p.in_target and c.type='video' and c.source in ('tiktok_top_ads','apify_tiktok_top_ads','tiktok_ad_library') and c.thumb_key is null
        and not exists(select 1 from creatives c2 where c2.product_id=p.id and c2.thumb_key is not null)
        order by c.product_id, c.position limit :n"""), {"n": lim})]
ok = 0
for i in ids:
    try:
        with SessionLocal() as db:
            c = db.get(Creative, i)
            if media.refresh_source(c):
                db.commit()
                ok += bool(media.grab_thumb(i))
    except Exception as e:  # source gone / rate limited: next one
        print("skip", i, str(e)[:80])
print(f"tiktok creatives tried {len(ids)} · thumbnails saved {ok}")
