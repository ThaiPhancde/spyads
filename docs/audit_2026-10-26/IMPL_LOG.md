# Implementation log — audit 2026-10-26

## infra

Design §3 OPS-DB, OPS-RUN, RETENTION; audit 04 F2 (heartbeat part), F4, F5, F6, F10, F13; 02 B5 (heartbeat part).

### Changed
- `apps/api/app/db.py:13-18` — no silent SQLite fallback: missing `DATABASE_URL` → `SystemExit` with a clear message, unless `ALLOW_SQLITE=1` (then `./market_intel.db` on purpose). All scripts/tests already set `DATABASE_URL` explicitly (conftest, `test_*.py`, `scripts/verify_*_browser.py`).
- `apps/api/app/db.py:101` `scheduler_lock()` — `pg_try_advisory_lock(727003)` on a connection kept for the process lifetime; `None` when another process holds it; `True` on SQLite.
- `apps/api/app/db.py:117` `migrate_all()` — the four additive migrations (workspace/auto_migrate, cluster, similar pg_trgm, personalization indexes) in one place.
- `apps/api/app/main.py:112-119` — startup calls `migrate_all()`; `:142-145` an in-process scheduler (no `DISABLE_SCHEDULER`) also takes the lock and does not start when a worker already holds it.
- `apps/api/app/worker.py` — takes the lock first (exits 1 with a log line if taken), runs `migrate_all()` (now includes similar + personalization migrations), unused `auto_migrate` import removed, docstring updated.
- `apps/api/app/realtime.py:450-490` — every scheduler tick writes `app_settings['scheduler_heartbeat']` = JSON `{at, pid, running, max_parallel, last: {job: iso}}` (from the `_last_*` globals); `read_heartbeat(db)` adds `age_s`. `:512` cleanup log also fires for table pruning.
- `apps/api/app/main.py:851-856` — `GET /api/health` returns `worker_heartbeat_age_s` (null if no scheduler ever ran).
- `apps/api/app/routers/intel.py:1513-1527` — `GET /api/collector/scheduler` reads the DB heartbeat when `DISABLE_SCHEDULER=1` (worker process), RAM otherwise. Same keys as before plus `last`, `heartbeat_at`, `heartbeat_age_s`, `process`.
- `apps/api/app/retention.py:226` `table_ttls()` + `:249` `prune_tables()`, called from `run_cleanup` (`:361`, result key `rows_deleted`). `{table: (column, days)}`, override `RETENTION_DAYS_<TABLE>`, negative = keep forever:
  user_sessions / auth_csrf_tokens / auth_rate_limits: expires_at, 0 (once expired) · search_jobs: created_at 60 · external_api_calls: created_at 120 (quotas are day/month) · page_fetches: requested_at 90 · raw_records: ingested_at 90 · price_jobs / engagement_jobs: finished_at 90 (queued/running rows have NULL and never match; freshness lives in observations) · mkt_notifications: created_at 180 · mkt_activity: created_at 365 · mkt_behavior_events: created_at = `PREFERENCE_MAX_AGE_DAYS` (365) + 30.
- `start.ps1:30` — hint now `app.workspace.cli create-user` (was the non-existent `create-admin`).
- `apps/api/tests/test_infra.py` — heartbeat round-trip (write → read → /api/health → /api/collector/scheduler via service token, with API RAM emptied) and retention (old rows deleted, fresh kept, behavior events inside the preference window kept, dry run deletes nothing).

### Skipped
- `advertiser_ad_snapshots`, `advertiser_activity_events`, `*_daily_snapshots`: the competitor tracker diffs against the latest snapshot and `mkt_notifications.event_id` references activity events; pruning needs a "keep latest per advertiser" rule, not a plain age cut.
- Autostart task (ONLOGON / restart-on-failure) and the UI "data stopped updating" banner: machine change needs user consent; UI out of this package's scope.
- Cross-process event bus (`pg_notify`, rest of B5) and lock re-acquire after a Postgres restart (marked `ponytail:` in `db.scheduler_lock`).
- `docs/auth-competitor.env.example:19` wording (F13 second bullet): docs, not touched.

### Verification
- `.venv/Scripts/python -m pytest tests/test_infra.py -q` → 2 passed.
- `.venv/Scripts/python -m pytest tests -q -x -p no:cacheprovider` → 401 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.
- `DATABASE_URL= python -c "import app.db"` → exits with the message; with `ALLOW_SQLITE=1` → sqlite.
- Live Postgres: a second process cannot get lock 727003 while the first holds it (lock released after).
- Live Postgres `prune_tables(dry_run=True)` → `{}` (every table/column exists; nothing old enough yet).

## auth

Design §0 "Quyền ghi của MKT", §3 AUTH-403 + RADAR-WATCH; audit 02 B1/B10, 03 F1/F2/F10/F13, 05 B1/FE-01/FE-02.

Changed:
- `apps/api/app/workspace/auth.py:132-134` `mkt_write`: MKT may now call `PATCH /api/products/{id}/name`, `POST .../name/confirm`, `.../name/undo`, `.../split`, `.../scan`, `.../merges/{eid}/undo`, `POST /api/products/merge`, `POST /api/products/image-matches/review`, `POST /api/creatives/{id}/pin`. Pipeline run, lifecycle, explain, agent, radar watch and image watch stay closed.
- `apps/api/app/db.py:163` `get_db` also puts the session user's name in `db.info["user_name"]`.
- `apps/api/app/main.py:540` new `_by(db)`: the session user's name, or `"service"` for the service token. Merge, rename, confirm, split, cannot-link and undo-merge now use it. `body.user` was removed from `MergeIn`, `NameIn` and `SplitIn`, and `ByIn` was deleted. An old client that still sends `user` has it ignored.
- `apps/api/app/main.py:657-658` product scan: limited to 20 per user per hour with `workspace.auth.rate_limit`, because each scan fetches up to 30 outside pages. The service token is not limited.
- `apps/api/app/main.py:709-724,770` Competitor Radar: `watched`, `watched_only` and the new `watched` count all come from the caller's own `Follow` rows. They are applied after the shared 60 s cache, so a follow shows at once and the cache key no longer holds the filter. The detail `watched` flag works the same way. The global `POST /api/competitor-radar/{aid}/watch` endpoint was deleted: it was 403 for every account and nothing called it any more. The `Advertiser.is_competitor_watched` column is still in the DB.
- `apps/api/app/routers/clusters.py`: removed the unused `ByIn` body from accept, reject and undo (they already used `db.info["user_id"]`), so the client can send no body.
- Web: `components/product-cluster.tsx`, `components/product-name.tsx` and `app/clusters/page.tsx` no longer send `user` or import `getUser`.
- Web `app/page.tsx`: removed the "Chạy pipeline hôm nay" button and the imports it used.
- Web `app/products/[id]/page.tsx`: removed the "AI giải thích" button and the lifecycle transition buttons. Lifecycle history is still shown, read-only.
- Web `app/competitor-radar/page.tsx:29,69`: "Theo dõi" now calls `POST`/`DELETE /api/competitors/{id}/follow`. The "Đang theo dõi" stat reads `data.watched`, which counts every row, not just the current page.
- Web `components/layout/app-sidebar.tsx:40`: a nav group is hidden when all of its items are filtered out, so there is no empty "Hệ thống" label.
- Tests: new file `apps/api/tests/test_auth_mkt.py` with 3 tests:
  - newly opened routes are not 403, while pipeline/run, lifecycle and explain are 403;
  - a rename is attributed to the session user even when `body.user` is forged;
  - the radar's watched rows, count and detail flag are per account.
  
  Removed `test_watching_a_competitor_shows_in_the_watched_list_at_once` from `tests/test_engagement.py`: it tested the deleted global flag, and the per-user test above covers the same "shows at once" case.

Skipped:
- Exposing `mkt_write` to the UI as a `can: [...]` list (audit 03 recommendation 1): not in this package. The buttons for system actions are simply hidden.
- F10 (`/agent` and `/data` pages are dead): they were already flagged `admin` and filtered out of the sidebar. The routes themselves were left as they are.
- `routers/intel.py` `QueryIn.user`: it is under `/api/collector/`, which only the service token can call, so it is not an MKT trust issue.

Tests:
- `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider` → 403 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.
- `npx tsc --noEmit -p .` (apps/web) → no errors.

## integrity

Design §3 CACHE-UID, ALLOC-MERGE, VOTE; audit 06_critic.md C1, C2, C3.

### Changed
- `apps/api/app/routers/intel.py:914-915` — `/api/discovery/tab/{tab}` cache key now includes `db.info.get("user_id")` (loved / saved / favorites / `loved_untested` are per user). Other `cached()` calls (`main.py` pulse, hidden-v3, competitors; `intel.py` radar, product_radar) re-checked: none reads `user_id` / Vote / AdInteraction / Follow inside the cached function (competitors' `watched` is applied after the cache by the auth package).
- `apps/api/app/routers/intel.py:865-879` vote: `rate_limit(f"vote:{uid}", 120, 3600)` per user (called before the first query, otherwise SQLite deadlocks on its own write lock; service token not limited). Dedupe is now `(product, user_id)`: `market` is stored (latest value) but no longer part of the key, so it can't stack votes.
- `apps/api/app/services/discovery.py:272-274` — `mkt_appeal` = mean over voters (latest vote per `user_id`, ordered by `created_at, id`), not over rows. NULL `user_id` (service token / pre-account rows) counts as one voter, matching the endpoint's dedupe.
- `apps/api/app/workspace/migrate.py:20,73-80` — `one_vote(db)`, run on every startup from the existing workspace migrate hook: deletes older rows per `(product_id, user_id)` (NULL = one voter), keeps the latest. Idempotent, portable SQL. Live PG dry run (SELECT only): selects exactly row id 1 (product 1, `loadtest`, older than id 2 `e2e`). It will be deleted at the next API/worker start; not run by hand. No unique index: legacy `merge_products` moves votes between products and would then 500.
- `apps/api/app/services/cluster_membership.py:72-78` `join()` (the single path for auto-merge and manual accept) — expires stale reservations, then raises `ValueError("allocation_conflict")` when both sides hold allocations and more than one user is involved. `clustering.auto_merge` already turns a non-hard `ValueError` into `status="pending"` + blocker, so the pair lands in the MKT review queue with `allocation_conflict`, and is retried next run (merges once one side releases).
- `apps/api/app/routers/clusters.py:83-85` — manual accept of such a pair returns 409 "Hai sản phẩm đang được hai MKT khác nhau nhận nên không gộp được. Gộp lại sau khi một bên trả sản phẩm."
- `apps/api/app/allocation.py:177-188` `claim()` — if the caller already holds an allocation in the scope it is returned (prefer the one for this `product_id`), free products are still locked onto it; 409 `taken` only when the caller holds nothing there. Fixes clusters merged before the guard (two owners): each owner can still start-test their own product.
- `apps/api/tests/test_integrity.py` — the three critic repros, flipped: B gets `[]` from favorites after A loved P; two claimed products are not auto-merged (`allocation_conflict` pending), accept → 409, and after a forced pre-guard merge both owners start-test → 200; 5 votes with different `market` → 1 row, per-user mean (3 voters → 85), `one_vote` idempotent and keeps the latest (incl. NULL-user rows).

### Skipped
- `market` whitelist validation: ignoring it in the dedupe key already removes the stacking; markets.py has no single "valid vote market" list.
- Legacy `POST /api/products/merge` (`entity_resolution.merge_products`) moves ads/votes between products without the allocation check: not cluster membership, out of this package; same ceiling as before.
- `main.py:658` product scan calls `rate_limit` after a query → same SQLite lock issue as the vote endpoint had (Postgres fine); not in scope.
- Review-queue UI label for the `allocation_conflict` blocker: frontend, out of scope (raw code is shown).

### Verification
- `.venv/Scripts/python -m pytest tests/test_integrity.py -q -p no:cacheprovider` → 3 passed.
- `.venv/Scripts/python -m pytest tests -q -x -p no:cacheprovider` → 406 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.

## ssrf-fx

Design §3 SSRF + FX; audit 02 B4, B9 (FX part).

### Changed
- `apps/api/app/netsafe.py` (new): `check_public_host` / `check_url` (moved from visual/service.py), `read_capped`, `safe_stream()` (httpx, `follow_redirects=False`, host checked before EVERY hop, one client so cookies survive), `safe_get(url, *, max_bytes, timeout, headers, impersonate=None, max_redirects=4)` → `Fetched(status_code, headers, content, url, .text, .json())`. `impersonate="chrome"` uses one curl_cffi `Session` with `allow_redirects=False` + `stream=True` and the same per-hop check. Rejects non-http(s), private, loopback, link-local, reserved, multicast and unspecified IPs. `FetchError(code)` is a ValueError.
- `apps/api/app/visual/service.py:30,47,113-122`: the duplicate SSRF code is gone. `ImageError` is now an alias of `netsafe.FetchError` (same codes, so every `except ImageError` also catches the guard). `check_public_host` is re-exported for jsonld.py and test_image_search.py. `fetch_image` runs on `safe_stream` + `read_capped`.
- `apps/api/app/services/spy.py:112-116`: `_get` (fetch_landing, its Shopify `.json` fallback, AliExpress reviews) → `safe_get(..., impersonate="chrome", max_bytes=8 MB)`. Impersonation is kept, redirects are followed by hand.
- `apps/api/app/pricing/extract.py:26-48`: `_fetch` → `check_url` + `safe_get` (10 MB cap, text still cut to 3 MB). robots.txt is read with a non-following client (a redirected robots.txt is treated as allowed, never followed). The old first-host/last-host-only check is removed. `:79` `shopify_json` → `safe_get` (5 MB).
- `apps/api/app/media.py:82` `_download` → `safe_stream`; `:296` video poster → `safe_get` (20 MB); `:395` `grab_thumb` → `safe_get` (20 MB).
- `apps/api/app/creative_enrich.py:150,162` `_clip` → `safe_stream`. A blocked URL returns `b""` ("decided"), so it is not retried every tick.
- `apps/api/app/services/enrichment.py:304-308` `to_usd` returns None for a currency missing from FX_TO_USD (no more ×1). A missing currency is still read as USD, as before. `:291` `price_segment` goes through `to_usd` (unknown → None).
- `apps/api/app/services/features.py:116-117,208`: drop None conversions before sum/min (avg_price, price_trend, supplier_price_usd). Callers checked: `main.mkt_summary` already filters falsy `usd()` and only converts verified observations whose currency is in FX_TO_USD; `scoring.margin_potential` already handles None.
- `apps/api/tests/test_netsafe.py` (new, 10 tests): blocks 127.0.0.1, 10.x, 169.254.169.254, ::1, ftp:, file: with nothing requested; a redirect to a private host is refused before that hop; a public redirect is followed and the byte cap applies; the curl_cffi path refuses a 302 to the metadata IP; `to_usd('XYZ')`/JPY is None.

### Skipped
- DB ECB rates in `to_usd`: no existing helper combines "DB rate, else static table" without a DB session (`eu_market.service.fx_table(db)` needs one), so this is the None fix only. A `ponytail:` comment marks it.
- `pricing/router.py:64 plausible()` still uses `FX_TO_USD.get(cur, 1)` for its $0.5–5000 range gate. It is a gate, not a stored conversion, and outside the named callers. Switch it to `to_usd` (None → skip the range check) if wanted.
- `eu_market/connectors/jsonld.py fetch_product` still uses `follow_redirects=True` after the first-host check. EU code is hidden and not in this package's list. Move it to `safe_stream` when EU comes back.
- B9 "spy should call pricing.extract" (merging the two landing-price pipelines) is outside this package.
- DNS rebinding (the name is resolved again by the client) is marked with a `ponytail:` comment in netsafe.py.

### Verification
- `.venv/Scripts/python -m pytest tests/test_netsafe.py -q -p no:cacheprovider` → 10 passed.
- `.venv/Scripts/python -m pytest tests -q -x -p no:cacheprovider` → 416 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.
- Live smoke test: `safe_get('http://github.com/')` follows http→https on both the httpx and curl_cffi paths (200). `spy.fetch_landing('http://localhost:8000/')` → `FetchError: localhost`.
- `apps/api/test_image_search.py` (script): its SSRF section passes. It then fails at line 172, the compare_prices `stale` assert: that test data uses a fixed 2026-10-08 date that is now more than 14 days old. This was already failing and is unrelated to this change.

## freshness

Design §2.1; audit 01 F1, F2, §1.1; 05 CMP-F1.

Changed:
- `apps/api/app/services/adsignals.py:32-59`: added `ACTIVE_GRACE_DAYS` (env, default 3) and `ran_until(a, now)`. For a stopped ad it returns the stop date. For an "active" ad it returns max(last_verified_at, last_seen_at) + grace, capped at now. Also added `verified_live(a, now)` (active and confirmed within the grace window) and `verified_live_sql()` (the same check as an Ad filter). `days_running` now counts to `ran_until`. `is_active` is never flipped.
- `apps/api/app/services/features.py:51` `_active_at`: an ad counts as active on day d only if `ran_until(ad, end(d)) >= end(d-1)`. Historical backfills stay correct because `ran_until` is capped at as_of. `:164` `longevity_days` uses `ran_until`. Since `active` drives force_top/max, strong_ads, advertiser_count/hhi, markets, engagement, unique_ads and variants, demand, TEST_NOW and the radar quadrants now count only verified-live ads.
- `apps/api/app/services/features.py:20,86`: `COLLECTED_DATE_SOURCES = {"tiktok_top_ads"}` is excluded from `new_ads_7d` and `new_ads_prev_7d`. Both are excluded so `creative_acceleration` stays consistent. There is a `ponytail:` note: the check uses a source list, not a per-ad date-kind column.
- `apps/api/app/services/hidden.py:27,80,88`: Hidden Winner `active`, the scope subquery and `active_ids` use `verified_live`. `:75-76` load_only now includes last_seen_at, last_verified_at and inactive_at, which avoids N+1 lazy loads.
- `apps/api/app/services/discovery.py:156`: `market_dna` counts only verified-live ads.
- `apps/api/app/routers/intel.py:1390-1393`: `/api/markets/live` counts active ads only when verified live. `:1470`: Product Radar `live` uses `verified_live`.
- `apps/api/app/main.py:387,421`: `mkt_summary` running days (longest/median, top_ads) now use `days_running` instead of counting to now.
- `apps/api/app/ingest.py:282-286`: the refresh branch moves `ad.first_seen_at` earlier when the incoming `rec.first_seen` is earlier, and pulls `product.first_seen_at` back with it. It never moves the date forward.
- `apps/api/app/realtime.py:44,56,139,141`: scheduled syncs (`_params_from`, `_connector_param_list`, used by tracked queries, connector syncs and the pipeline) send `active_only=False` for the Meta adapters (`meta_library`, `meta_graph`, `apify_meta`). Those adapters map `is_active`/`end_date` (adapters/meta.py:174-176, 317-318). `limit` is unchanged, so volume stays bounded. Live search (`_run_search`) still uses `active_only=True`.
- `apps/api/app/realtime.py:666`: `_daily_job` runs `adsignals.refresh` before `score_all`, so the stored `force_score` follows the capped running days. Before this change it was recomputed only at API startup and on ingest.
- `apps/api/tests/test_freshness.py` (new): checks that an ad last verified 20 days ago is capped (43 days, not 60), drops out of active_ads/longevity and is still `is_active`; that a verified ad keeps counting (60 days); that refresh moves first_seen earlier on both the ad and the product, and never later; and that scheduled Meta syncs use active_only=False.
- `apps/api/tests/test_hidden_evidence.py:144`: `test_market_filter_keeps_global_advertiser_count` used a fixed NOW (2026-10-14) while `compute()` uses the real clock. Its ads were 12 days unverified, so under the new rule they are correctly not live. The test now gives them a fresh `last_seen_at`.

Skipped:
- `new_advertisers_7d` and `new_ads_30d` still use TikTok collected dates. The task named only `new_ads_7d`, and changing these would move the advertiser-growth semantics.
- `force()` still applies ×0.7 only when `is_active=False`. An unverified ad is "unknown", not "stopped". Its time component is already capped.
- Display-only `is_active` uses are unchanged: ad cards, library, personal_search, creative_enrich, and the `active` counts in `mkt_summary`. They show the source's state, and the counting paths now use `verified_live`.
- The audit idea of `status=unverified` after liveness miss ≥2 was not done. The `verified_live` derivation covers it with no new column.
- TikTok has no liveness. Its ads stay live only for ACTIVE_GRACE_DAYS after their last re-collection, which is the intended honest behaviour.

Live DB (Postgres `toolspy`, 26/10, API and worker not running):
- Before the rescore: 16,138 `is_active` ads, of which only 3,743 are `verified_live`.
- Ran the `_daily_job` scoring steps (`adsignals.refresh` on 16,912 ads, then `score_all(commit_every=50)` on 12,313 products) in 84 s. Snapshots were not rebuilt; the worker's daily job does that. Average opportunity_score went from 26.91 to 24.98. Recommendations are now SKIP 12,137 / WATCH 95 / TEST 81.
- The API and worker must be restarted to load the new code. Until then, a running instance would recompute with the old rule.

Tests:
- `.venv/Scripts/python -m pytest tests/test_freshness.py -q -p no:cacheprovider` → 3 passed.
- `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider` → 419 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.

## video-identity

Design §2.2; audit 01 §1.5 (CMP-G3), F3, C4 (partly).

### Changed
- `apps/api/app/visual/models.py:26` — new nullable, indexed column `product_images.video_key` (String 16). auto_migrate adds it.
- `apps/api/app/videos.py` (new):
  - `:33 assign(db)` buckets hashed creative covers. A cover takes the nearest existing key within `VIDEO_DIST = 6` bits (numpy `bitwise_count` against each group's first pHash), else its own pHash starts a new video. Ordered by creative id. `TRIVIAL_PHASH` covers (blank / single-bit frames) get no key. Only rows whose creative still exists are touched. It is additive: it writes only `video_key IS NULL` rows, in bulk UPDATE chunks of 2000.
  - `:100 facts_for(db, keys, with_ads)` covers every external ad on the key (`ADS_ONLY`, not internal, any network, active or inactive). It returns `ads`, `pages` (from `features.adv_key`), `pages_unknown_id`, `verified_live_pages` (from `adsignals.verified_live`), `new_pages_7d`, `video_first_seen` = min `first_seen_at`, `networks`, `products`, and `generic` (more than `clustering.MAX_KEY_PRODUCTS` products, the same template / stock-frame rule clustering uses). It also returns `entrants`, sorted by first_seen, each with `new_this_week`. TikTok Top Ads are never marked new, because their date is the collection day (`features.COLLECTED_DATE_SOURCES`). With `with_ads=True` it also returns `ad_list`.
  - `:114 product_video_facts(db, pid)` returns `{count, primary, videos}`. `videos` is the top 5 sorted non-generic first, then by pages. `primary` is the non-generic video with the most pages.
  - The CLI `python -m app.videos backfill` runs `auto_migrate()` (additive) and then `assign`.
  - `ponytail:` notes in the docstring: the key comes from the cover frame only (re-uploads with a new cover split; upgrade path = hash several frames of the SD rendition), and groups are leader buckets with no transitive merge.
- `apps/api/app/visual/service.py:434-437` — `index_pending()` calls `videos.assign(db)` after hashing, so new covers are keyed each scheduler tick (`_visual_wrapper`). The result gains `video_keyed`.
- `apps/api/app/routers/videos.py` (new) — `GET /api/videos/{key}` returns the facts plus `ad_list`, or 404. Requires sign-in like the other read routers.
- `apps/api/app/main.py:84-87` mounts the router. `:502` `product_360` gains `"video_facts"`.
- `apps/api/tests/test_videos.py` covers:
  - two creatives 1 bit apart, on different pages and products, share one key;
  - a far frame becomes another key, and a blank frame gets none;
  - a second `assign` keys 0 rows;
  - facts count 2 pages, 1 verified live page, 1 new this week, and first_seen = the stopped ad's date 40 days ago;
  - `/api/videos/{key}` returns 200 with 2 ads, and an unknown key returns 404;
  - product detail `video_facts.primary` is that key.

### Live DB (run 2026-10-26)
- `python -m app.videos backfill` → added the column and index (the only pending schema change; checked first), then keyed 25,652 rows in about 24 s. 19,998 distinct keys; 282 keys are run by ≥ 2 pages. A second run keyed 0 rows.
- Only 25.8k of the 99,261 creative `product_images` rows still have a creative. The other 73,477 rows are orphans whose creative row was deleted, and they are not keyed.
- The biggest key, `ede9c4c6d41c7225` (49 pages, 52 products), is a template frame and is flagged `generic`.
- `product_video_facts` on the product with the most ads (1473) takes 0.13 s.

### Skipped
- UI (product page card, Family button → video_key): API only in this package.
- Stock exclusion by ad count (`clustering.MAX_STOCK_ADS = 8`) is not used, because a real winning video is run by more than 8 ads. The product-spread rule (`generic`) is used instead.
- TikTok ads with no page id ("unverified:ad:…" advertiser per ad) each count as one page, the same as `features.adv_key`. They are reported in `pages_unknown_id`, and no merge by name is guessed.
- Orphan `product_images` rows (73k) are a retention / cleanup issue and out of scope.

### Tests
- `.venv/Scripts/python -m pytest tests/test_videos.py -q -p no:cacheprovider` → 1 passed.
- `.venv/Scripts/python -m pytest tests -q -x -p no:cacheprovider` → 420 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.

## landing-age

Design §2.3, audit 01 §1.2 (CMP-G2).

### Changed
- `apps/api/app/models.py:136-138` — `Store.page_platform` (String 32) + `Store.page_published_at` (DateTime), nullable; added on start by the existing `db.auto_migrate` additive-column path (no migration script needed).
- `apps/api/app/services/spy.py:112-146` — `PAGE_AGE_FLAG_DAYS = 14`; `detect_platform(html)` (cdn.shopify.com / Shopify.theme → shopify, shoplazza, ladicdn / ladipage → ladipage, wp-content + woocommerce → woocommerce, else unknown); `shopify_published_at(product)` (published_at, fallback created_at → naive UTC); `page_age_flag(store, video_first_seen)` → `{days_older, flag: days_older > 14}`.
- `apps/api/app/services/spy.py:156-180` — `fetch_landing` (still through `netsafe.safe_get` via `_get`) returns `page_platform` + `page_published_at`; on `/products/` URLs it now always reads `<handle>.json` when the page is Shopify (and, as before, when no price was found); the HTML price is kept, variants only fill a missing price.
- `apps/api/app/services/spy.py:300-304` — `scan` writes platform/date onto the Store rows of that landing (`domain` = landing host, `product_id` in the ads pointing there); "unknown" never overwrites a known platform; a missing date never clears a known one.
- `apps/api/app/main.py:331,469-470,500-502` — product_360 `stores[]` adds `page_platform`, `page_published_at`, `page_age` (`{days_older, flag}`) against the product's first video ad (any ad when it has no video).

### Skipped
- Shoplazza product-ID → date: not implemented (needs an ID↔date calibration table); `ponytail:` note in `spy.detect_platform`.
- No UI change (task scope = API); the product page can read `stores[].page_age.flag`.
- Existing Store rows get values only as `spy.scan` re-fetches landings (RECHECK_DAYS=14 / on-demand scan); no backfill run on the live DB.

### Tests
- New `apps/api/tests/test_landing_age.py` (10 cases: platform markers, Shopify date parsing, flag math incl. 14-day boundary, fetch_landing reads .json even when the HTML had a price).
- `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider` → 430 passed, 3 skipped.
- `python -c "import app.main; import app.worker"` → ok.

## checklist-tabs (finished inline after the workflow stopped)

The workflow session ended while this package was running. What it had written was checked and kept:
- `app/services/checklist.py`: 4-part win checklist (pass/fail/unknown + value) built from video facts, store page age,
  verified-live pages, and supplier listings. Payloads for a whole page take 4 queries.
- Product API: v1 scores are no longer returned (only old `?sort=win_score` bookmarks are mapped to opportunity).
- `/api/discovery/tab/*`: the tabs `video_win`, `video_scale`, `potential_tiktok`, `cash_cow`.
- Product page: Checklist card, Video card, store platform/date with the 14-day red flag.
- Supplier price label: "Giá nguồn thấp nhất (1688 / Taobao / AliExpress)".

## ui-hygiene (inline)
- Copy:
  - Vault, `/ads` and nav hint now match retention (thumbnail kept, video same-day).
  - `retention.py` docstring no longer says "3000 videos/day".
- `site-header.tsx`: the title is a `<p>`, so each page has one `<h1>`.
- `Loading` only adds "kiểm tra API đang chạy" for network / 5xx errors. Radar chart shows its error instead of a skeleton.
- Visual search:
  - `eu_listing` is always a source labelled "Listing đã lưu" (these are mostly Google Lens saves).
  - The forced `sources` override was removed.
  - compare-prices gets the same `sources`.
  - The EU layout no longer shows env instructions.
- `apps/web/Dockerfile`: `ADMIN_TOKEN` ARG/ENV removed (the web never reads it).
- Repo, index only, nothing committed:
  - `.gitignore`: `.next*/`, `*.tsbuildinfo`, `*.out`, `demotheme/ui/`.
  - `git rm --cached` for `.next-library-test`, `.next-search-test` (113 MiB), `tsconfig.tsbuildinfo`, and the `demotheme/ui` gitlink.
  - Deleted: `calib_clip_full.out`, `eval_clip.out`, `headless_top_ads_sample.json`.
- Legacy root scripts: all 18 were run on a temp SQLite DB.
  - 5 pass and are kept: `test_ali1688`, `test_eu_public_sources`, `test_meta_ssr`, `test_spy`, `test_tiktok_commercial`.
  - The 13 broken ones were deleted.

## follow-ups (skipped items from the packages above)
- **Legacy `POST /api/products/merge`**: now returns 409 when the two products are held by two different MKTs. Test: `test_integrity.py::test_legacy_merge_refuses_two_owners`.
- **Product scan**: `rate_limit` runs before any query (avoids the SQLite lock).
- **`pricing/router.plausible`**: uses `to_usd`. An unknown currency skips the range check and is never ×1.
- **`features.py`**: `new_advertisers_7d` and `new_ads_30d` exclude collection-dated TikTok Top Ads. Test added in `test_freshness.py`.
- **Retention**: deletes orphan `product_images` rows whose creative is gone. Ran on live PG: **73,477 rows deleted**. Snapshot tables were measured (advertiser snapshots ≤ 2 per ad; daily snapshots are history used by the trend charts) and left alone.
- **Vault "Family" (dead dHash)**: replaced by `video_key` "Cùng video · N".
  - `/api/creatives?video_key=`, plus `video_key` / `video_size` on creatives.
  - The old full-table GROUP BY is gone.
- **EU `jsonld.fetch_product`**: goes through `netsafe.safe_stream`, so every redirect hop is checked.
- **FX**: `enrichment.refresh_rates(db)` loads ECB rates (29 currencies) at API start and before the daily re-score; static table as fallback. Test added.

## Meta liveness via the public Ad Library (user: "kiểm tra ad … tự mở web check")
Tested live on 2026-10-26, no cookies needed:
- **Page scan in ALL statuses**: Meta returns stopped ads with their end date. Changing `active_only=True` → `False` made the first page scan confirm 9 stopped ads.
- **Lookup by ID**: an Ad Library keyword search for the numeric Library ID returns exactly that ad with status and dates. The `?id=` page itself answers 400 without a doc_id.

`realtime.verify_liveness`:
- Page scans now ask for ALL statuses. An ad Meta reports stopped gets `is_active=False` and `inactive_at` = Meta's end date. Siblings with unknown status are not counted as alive.
- New `_lookup_misses`: up to `LIVENESS_ID_LOOKUPS` (30) per run. It covers every active Meta ad not confirmed within `ACTIVE_GRACE_DAYS`, including ads on pages too big to finish in 5 result pages. Followed pages are excluded.
- Test: `tests/test_liveness.py`.

Live run (12 pages): **244 verified, 24 confirmed stopped by Meta**, 0 errors, 190 s. It also exposed that live PG lacked the `stores.page_*` columns until `migrate_all()` ran; that is fixed.

## Shoplazza page age (user: "có cách nào đối chiếu không")
- Web search found no public docs; the Open API is admin-only.
- Probed 200 real ad landings: 71 Shopify, 5 Shoplazza (CDN `img.staticdj.com`, often without the word "shoplazza"), 10 Woo, 5 Ladipage, 2 Shopline, 3 Funnelish.
- Shoplazza product IDs are **UUID v4 (random), so no date can be read from the ID**. The product page **embeds the product's own JSON** with `created_at` / `published_at` (also `sales` and a `fake_sales` flag).
- `spy.shoplazza_published_at(html, handle)` reads it from the object of THIS handle, bounded before the next product object (recommended products follow). `detect_platform` also recognises `staticdj.com`.
- Live: klasyczny.shop → shoplazza, published 2025-10-16, 169 PLN. Test added in `test_landing_age.py`.

## Discovery pages merged (design §3b, user-approved)
- `/radar?view=channel|discovery|live|low-competition|table|markets`. The views are the old pages moved to `components/discover/*`.
- `/product-radar`, `/hidden-winners`, `/products`, `/markets` redirect and keep their query string. `/products/[id]` is untouched.
- Nav has one item, "Khám phá sản phẩm".
- Changed views:
  - Table view is paged (`usePaged`, 100 per page); the hard 300 limit is gone.
  - Hidden Winners is renamed "Ít cạnh tranh (đang được test)".
  - Discovery drops the favorites/saved/hidden tabs.
- Discovery tab cache: only favorites/saved are per user. The other tabs share one result (~3–5 s on 12k products); the viewer's `loved_untested` flag is added after the cache.
- Static tab labels, so raw keys never show while loading.
- Worker writes its heartbeat before the inline cleanup/re-score, so a fresh worker shows in `/api/health` at once.

## verify (2026-10-26, inline)
- `pytest apps/api/tests` → **437 passed, 3 skipped**. `import app.main; import app.worker` → ok.
- `tsc --noEmit` → clean. `next build` → ok, all routes built.
- Live app:
  - Started API, worker and web through one-shot scheduled tasks (`apps/api/data/run/run_*.ps1`; no autostart, per the user).
  - `/api/health` → `worker_heartbeat_age_s` 22.8.
- Browser (Python Playwright, live data, scratchpad `verify_live.py`) → **26/26 checks passed**:
  - register → login;
  - sidebar has no EU Market, "Hệ thống" or old pages;
  - one `<h1>`;
  - 4 channel tabs and 6 views render;
  - 4 old routes redirect;
  - product page shows checklist + video + page cards;
  - visual search has no "EU Market";
  - `/eu-market` blocked;
  - Vault copy fixed.
  - Only errors: 401 on `/auth/me` before login, which is expected.
  - The 4 QA accounts (`qa-claude-*@levelupvn.com`) were locked with the CLI afterwards.
- Data right now: worker was down 23→26/10, so Video Scale and Breakout are 0. Video Win has 15, Tiềm năng 60, Bò sữa 60. They fill as the worker's tracked queries (now ALL statuses) and liveness run.

## scan errors + slow ad growth (user: "kiểm tra lỗi khi quét … lượng ads tăng còn chậm")
- **Liveness starved (fixed)**: the light jobs (visual / price / engagement / naming) were counted against
  `MAX_PARALLEL_JOBS=3`, so liveness and the source syncs rarely got a slot. Liveness never ran after a worker restart.
  `realtime._heavy()` now counts only `c:` / `q:` scans + liveness. Test: `test_infra.py`.
- **Root cause of slow growth (fixed)**: every tracked query always read the same first `limit` (40) ads.
  - Live: 26 of the re-runs today fetched 40 and stored **0 new**. The 460 new ads came only from queries running for the first time.
  - Fix: `TrackedQuery.cursor` stores the Meta paging state per country. The next run continues deeper.
  - It starts over at page 1 when results end, when the cursor is older than `TRACKED_RESUME_HOURS` (6), or when Meta refuses the cursor.
  - Live, same PH query 3× in a row: **0 → 33 → 38 new**. Test: `tests/test_tracked_paging.py`.
- **`tiktok_commercial` (no keywords configured)**: it was skipped every tick without a `last_sync_at`, so it stayed "most overdue" and was re-picked every 30 s. The skip now stamps the time.
- **"landing_failed ≈ 50 %"** was mostly not failures. In a sample, the failing pages were HTTP 200 brand / home / category / tracking pages with no product price; only a few were 403 bot blocks. The spy stats now split `landing_failed` (error / HTTP ≥ 400) from `landing_no_price`.
- Worker log: no tracebacks. The only stderr noise is the SQLAlchemy `vector` type warning, wrapped by PowerShell as NativeCommandError.
- `pytest` → 440 passed, 3 skipped.
