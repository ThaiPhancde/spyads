# Global Image Search & EU Market rebuild — audit, design, benchmark, tests, status, ops

Spec: the "SPYADS — GLOBAL IMAGE SEARCH & EU MARKET REBUILD" brief (2026-10-09). Implemented 2026-10-09 (P0–P4, parts of P5).
Companion files: `docs/image_search_eval_hash.json` (+ `_hash+clip.json` once the embedding backfill is complete),
`docs/sql/pgvector_migration.sql`, `apps/api/test_image_search.py`, `apps/api/test_eu_public_sources.py`, `apps/api/eval_image_search.py`.

## P0 — Audit (2026-10-09)

**Runtime.** Windows 11, AMD Ryzen 5 5600H (6C/12T), 20 GB RAM (~7 GB free with API + worker + web dev running),
RTX 3050 4 GB (not used — spec: no GPU). Python 3.11 venv at `apps/api/.venv`. PostgreSQL 18.6 (`D:\PostgreSQL\18`),
db `toolspy` 254 MB. API (`uvicorn app.main:app`) + scheduler (`python -m app.worker`) are separate processes. (Each shows
twice in the process list: the venv `python.exe` is a launcher whose child is the real interpreter — not a duplicate.)

**pgvector.** Not installed and not installable here: `pg_available_extensions` has no `vector`; the VS 2022 folders
are empty shells (no `cl.exe` / `vcvars64.bat`), and `D:\PostgreSQL\18\lib` is not writable without admin. →
fallback (spec §4.2): embeddings stored as BYTEA (float16) in Postgres, cosine search over an in-process numpy
matrix with a documented ceiling; `docs/sql/pgvector_migration.sql` is ready for when the extension is available.

**Existing image data (reused, not rebuilt).**
| table | rows | images |
|---|---|---|
| `creatives` | 80 826 | 72 361 have a stored ≤480 px JPEG thumbnail (`thumb_key` → `apps/api/data/thumbs/<2>/<sha>.jpg`, 664 MB); 59 373 had a custom dHash (`phash` column, media.py, creative families) |
| `products` | 5 893 | `image_url` only (source CDN, expires) + `cover_creative_id` |
| `ads` | 9 795 | `media_url` only |
| `eu_listings` | **0** | `thumbnail_url` referenced, never downloaded |
| `eu_products` / `eu_price_snapshots` / `eu_sync_runs` | **0** | EU Market had never synced: every connector (Kaufland, bol, BigBuy) needs a seller / paid account |
| `eu_fx_rates` | 29 | ECB rates work |

Creatives by network: meta image 53k / video 23k, tiktok 2.8k, aliexpress 1.2k, 1688 0.7k, taobao 82 → the internal
index covers ads (Meta/TikTok) and China-source listings (AliExpress/1688/Taobao). The pipeline is "metadata + small
thumbnail only" (no bulk media, no FFmpeg) — image search hashes the stored thumbnails.

**Reused code.** `media._thumb_jpeg` / `grab_thumb` / `store()`; `/api/search` keyword logic (`split_keywords`);
`eu_market.service.fx_table` / `to_eur` (ECB), `listing_dict`, `analytics.compare_offers` / `landed_cost` / `price_series`;
`eu_market/connectors/base.py` contract; `realtime.scheduler_loop` (worker); `AdminGuard` (X-Admin-Token) + `WEB_ORIGIN` CORS;
Next 14 + shadcn/ui, `lib/api.ts`.

**Libraries.** Pillow 12.3 + ImageHash 4.3.2 + numpy 2.x were already installed (hash tier needs nothing new). Embedding
tier: `onnxruntime` 1.31 (CPU wheel, 15 MB) + the CLIP ViT-B/32 vision tower exported to ONNX by Xenova
(`Xenova/clip-vit-base-patch32`, MIT; fp32 352 MB and int8 89 MB) — no torch / transformers; preprocessing in Pillow.

## Design

1. **One index table `product_images`** (spec §7 ProductImage) keyed by `(source, source_id)` → `creative` / `eu_listing`
   rows, with provenance (`image_url`, `observed_at`), `thumb_key`, `image_sha256`, `phash`, `dhash`, `embedding`
   (float16[512]), `emb_model`, `processing_status`. No product rows are copied. `product_matches` (spec §7 ProductMatch)
   stores reviewed links (confirm / reject from the UI).
2. **Tiers** (spec §3.2, never "same product" from looks alone):
   `EXACT` = same sha256 or pHash/dHash Hamming ≤ 6 · `LIKELY_SAME_PRODUCT` = visual + metadata agreement (same product
   cluster as an EXACT hit; with CLIP: cosine ≥ 0.95 and hash ≤ 14 or metadata) · `VISUALLY_SIMILAR` = hash ≤ 14 or
   cosine ≥ 0.80. Only EXACT / LIKELY_SAME enter the price-comparison table; the rest go to "Similar products".
3. **Search is synchronous** in the API (hash + one embedding < 1 s; sync endpoints run in FastAPI's threadpool, never
   on the event loop). **All indexing runs in the worker** (`realtime._visual_wrapper`, one batch per minute:
   `IMAGE_INDEX_BATCH=400` creatives from local thumbnails, `IMAGE_INDEX_EU_BATCH=40` EU thumbnails downloaded, then
   `IMAGE_EMBED_PER_TICK=400` embeddings). Checkpoint = rows written; idempotent per `(source, source_id)`.
   `GET /api/search/images/status` reports coverage instead of per-search job ids (deviation from §9 — a search never queues).
4. **CLIP lifecycle**: lazy load on first use, batch 16, `IMAGE_EMBED_THREADS=4`, dropped after `IMAGE_EMBED_IDLE_S=600`
   idle (a daemon reaper thread). `IMAGE_EMBED_ENABLED=0` = hash-only mode (everything still works).
5. **Vector search without pgvector**: all embeddings as one float32 matrix in RAM (72k × 512 ≈ 150 MB, ~10 ms / query,
   incremental reload); ceiling ≈ 1M rows → run `docs/sql/pgvector_migration.sql` and replace `EmbIndex.topk`.
   Hash index: numpy uint64 + `np.bitwise_count` over every row (~2 MB, ~1 ms).
6. **EU Market public-data-first** (spec §6): `jsonld` (any public product page, no credentials) added; Kaufland / bol / BigBuy stay **optional** seller connectors. Status vocabulary:
   `available` → `ready` / `missing_credentials` / last run code; `access_restricted`; `unsupported_capability`;
   `rate_limited`; `temporarily_unavailable`. No CAPTCHA / login / anti-bot bypass: a 403 is reported as `access_restricted`.
7. **Security** (spec §9): real MIME by decoding (Pillow), 8 MB / 30 MP caps, SSRF guard (http(s) only, every resolved
   address must be public, re-checked per redirect hop, ≤ 3 redirects, 10 s, byte cap while streaming, image/* only),
   no temp files (bytes in memory, query pictures are never stored), per-IP rate limit 30 / min, request ids in responses
   and logs, machine-readable error codes, writes behind `AdminGuard`.

## What was built

**Backend** (`apps/api/app/visual/`): `models.py`, `service.py` (normalise, hash, SSRF fetch, HashIndex, search, tiers,
hydrate owners with their *own* thumbnail / price / link, `compare_prices`, worker indexers, image watchlists),
`embed.py` (ONNX CLIP, EmbIndex, `embed_pending`), `routes.py`:

| endpoint | notes |
|---|---|
| `POST /api/search/images` | multipart: `file` \| `image_url` \| `creative_id` \| `ad_id` \| `product_id` \| `eu_listing_id` + `mode`, `keywords`, `country`, `sources`, `price_min/max`, `sort`, `limit/offset`, `crop=x,y,w,h` |
| `POST /api/search/images/by-url` | image URL **or product page URL** (JSON-LD → listing saved to EU Market + its picture becomes the query) |
| `POST /api/search/images/from-product` | picture of an entity already in SpyAds (its own creatives are excluded from the results) |
| `GET /api/search/images/status` | coverage, engine, thresholds, limits |
| `POST /api/search/images/watch` | watch the product cluster of a picture (EU watchlist with `images`; `IMAGE_MATCH_NEW` alerts) |
| `POST /api/products/visual-match` | = from-product with `mode=same_product` |
| `POST /api/products/compare-prices-by-image` | price table: same-SKU vs similar, retail incl. VAT / VAT unknown / wholesale excl. VAT, total only when shipping is known, stale flag, ECB conversion with date |
| `GET /api/products/{id}/similar` · `/price-comparison` · `/image-matches` | product-centric views |
| `POST /api/products/image-matches/review` | confirm / reject a match |
| `POST /api/eu-market/listings/from-url` | public product page → EU listing (+ picture indexed at once) |

Modes: `visual`, `exact`, `same_product`, `similar_product`, `ads_match`, `find_suppliers`, `compare_prices`.

**Frontend** (`apps/web`): page `/visual` ("Tìm bằng ảnh", in the sidebar) + `components/visual/`: `image-input.tsx`
(ImageUploadDropzone: drop / click / Ctrl+V / URL; ImagePreview; ImageCropDialog — drag a rectangle, the API crops),
`results.tsx` (VisualSearchFilters, ImageSearchProgress skeleton, ProductImageCard with tier badge + ×N for the same
picture reused across ads, VisualMatchResults grouped by tier, SimilarAdsPanel, PriceComparisonTable, IndexCoverage with
"Limited coverage"), `global-image-search.tsx` (camera button + dialog, `visualHref()`), `watch-dialog.tsx`.
Entry points: header camera (every page), camera next to the keyword box on `/search`, Ads Detail sheet buttons
(Tìm bằng ảnh · Tìm nguồn hàng · So sánh giá · Quảng cáo tương tự · Theo dõi), product page buttons, EU Product Explorer
(camera + "Thêm từ URL"), Price Intelligence ("So sánh giá bằng ảnh"), Rare Finds (camera), Data Sources (new statuses).

## Benchmark (this machine, CPU only, while the embedding backfill was running)

| step | measured |
|---|---|
| sha256 + pHash + dHash from a stored thumb | 130–250 images/s (1 core); full backfill of 72 356 thumbs: **446 s** |
| CLIP ViT-B/32 fp32 (onnxruntime, 4 threads) | 43 ms / image single, 35 ms batched; int8: 27 ms. Backfill rate 8.5 img/s while sharing CPU with the API + worker (≈ 2.4 h for 72k; one-off) |
| model load / RAM | fp32 load 0.6 s; session ≈ 0.5 GB RSS; unloaded after 10 min idle |
| hash index load (72k rows) | 1–2 s at startup (warm-up thread), then stamp checks only |
| embedding matrix | 72k × 512 × 4 B ≈ 150 MB RAM; incremental refresh |
| search latency (`eval_image_search.py`, 900 queries, hash engine) | **p50 16 ms, p95 226 ms, max 2.0 s** (first query after start = model + index load) |
| API end-to-end (`curl`, upload → JSON) | 0.3–0.9 s warm (hash + CLIP), 4 s cold |
| `/ads`, `/search` pages | unchanged — no new work on those requests; indexing lives in the worker |

## Test report

| check (spec §12) | result |
|---|---|
| Offline unit/API tests — `test_image_search.py` (validation, hash invariances, SSRF guard, index→search on SQLite with real thumbs, tiers, compare_prices, API surface incl. 401/404/400 codes) | **passed** (mock / local) |
| `test_eu_public_sources.py` (registry statuses, JSON-LD parser incl. @graph / AggregateOffer / string prices, robots.txt, SSRF) | **passed** (mock) |
| Existing suites `test_eu_market.py`, `test_ingest_rules.py`, `test_scoring_rules.py`, `test_search_keywords.py`, `test_media_rules.py`, `test_auth.py` | **passed** |
| TypeScript `tsc --noEmit` | **0 errors** |
| Migrations (`auto_migrate`: `product_images`, `product_matches`, `eu_watchlists.images`) | applied on the live Postgres |
| Ground truth, hash engine (`docs/image_search_eval_hash.json`, 150 real pictures × 6 variants, K=10): copies (q35 / half size / WebP) recall@10 **0.99 / 1.00 / 1.00**, EXACT at rank 1 **0.98**; 5 % crop **0.94**; 15 % crop 0.03, 25 % crop + brightness 0.01 | **local image search passed** — hashing covers copies and light crops only, as designed |
| Same picture under another product cluster: 12 % of queries (18/150) | data ambiguity (unmerged duplicates), not a hash error — the reason LIKELY_SAME needs metadata agreement |
| Other-angle proxy (a hash-far creative of the same product in the top 10): hash engine **0.0** (by construction) | re-measure with `--write` once embeddings cover the index → `docs/image_search_eval_hash+clip.json` (pending at time of writing: backfill ~30 % done) |
| Live UI (Playwright, `scratchpad/ui_check.py`): /visual renders coverage line, upload → 1 EXACT + 7 similar, Compare mode → honest empty state when no priced offer, header camera, ad sheet buttons, ad → /visual?ad_id → 17 look-alikes, EU camera | **passed** (no JS errors beyond a pre-existing Radix forwardRef warning in sheet.tsx) |
| Live connector — JSON-LD product page | **passed** on gymshark.com (title, EAN, 40 USD, in stock, picture indexed); decathlon.de / notino.de / conrad.de return HTTP 403 to a plain GET → `access_restricted` (no bypass); zara.de has no JSON-LD → `unsupported_capability`; bol.com → 403 |
| Real price comparison | **partially**: works on ads with a real price and on EU listings; the EU table has only the listings added by URL so far (0 before this work). Nothing is estimated: unpriced offers are listed without a price |
| No FFmpeg / video transcoding introduced | confirmed (hashing from stored thumbnails only) |

Thresholds in use: `IMAGE_EXACT_DIST=6`, `IMAGE_SIMILAR_DIST=14` (from the measured distances: resize / recompress ≤ 1 bit,
5 % crop median 8 / p90 13, 10 % crop median 16), `IMAGE_SAME_COS=0.95`, `IMAGE_SIMILAR_COS=0.85`.

**CLIP cosine calibration** (`apps/api/calib_cosine.py`, 4 000 live vectors = 7.8M unrelated-product pairs and 169k
same-product-different-picture pairs):

| | p50 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|
| unrelated products | 0.42 | 0.58 | 0.63 | 0.71 | 0.81 |
| same product, hash-far picture (noisy labels) | 0.57 | 0.70 (p90) | | | |

| floor | unrelated pictures passing | ≈ per query on 72k | same-product other pictures kept |
|---|---|---|---|
| 0.80 | 0.124 % | 89 | 1.7 % |
| **0.85** | 0.038 % | 27 | 0.8 % |
| 0.90 | 0.019 % | 13 | 0.4 % |
| 0.95 | 0.014 % | 10 | 0.1 % |

Reading: with ViT-B/32, *different photos of the same product* (other angle, other scene, text overlays) are not
separable from unrelated pictures by cosine alone — so the "other angle" promise holds only as VISUALLY_SIMILAR
candidates, never as a same-SKU claim; the residual 0.014 % at 0.95 are mostly identical pictures that sit under two
unmerged product clusters. Same-SKU therefore always requires metadata agreement (EAN / product cluster / exact hash).
Smoke test on one picture: resized copy 0.99, 10 % crop 0.93, 25 % crop + brightness 0.89.

## Live web discovery — Google Lens via SerpApi (2026-10-09)

Answers the gap of the first version: a picture that was **never collected** can now find products on the Internet.

**Flow (Hybrid = default on `/visual`).** Local search runs and renders first; then `POST /api/search/images/live` starts
(or reuses) a Google Lens job; the UI polls `GET /api/search/images/results/{job_id}`. Opening a page from a link
(`/visual?ad_id=…`, camera from any module) only reads the cache (`cache_only=true`) — quota is spent **only on an
explicit "Tìm" / "Tìm trên Internet (1 lượt)" click**. Modes: Hybrid / Chỉ trong kho (no quota) / Chỉ Internet.

**Code.** `apps/api/app/visual/lens.py` (connector, ImageSearchQuotaManager, normalize, match verification, save /
verify), tables `lens_searches` (cache = job, raw payload cleared after `LENS_RAW_RETENTION_DAYS`) and
`external_api_calls` (quota ledger: search / upload / cache_hit / refused). Web UI: `app/visual/page.tsx` (tabs
Trùng & có thể cùng SP · Sản phẩm tương tự · Khám phá trên Internet · So sánh giá), `components/visual/web-results.tsx`
(WebResultCard, QuotaPanel, ZoomThumb lightbox).

| endpoint | |
|---|---|
| `POST /api/search/images/upload` | validate + keep the picture 30 min in RAM → `image_ref` (one upload, many searches) |
| `POST /api/search/images/live` | file \| image_ref \| image_url \| entity id + `country`, `hl`, `lens_type` (all/products/visual_matches/exact_matches), `lens_q`, `crop`, `cache_only` → `{job_id, status, cached}`; 402 on budget, 503 when the key is missing |
| `GET /api/search/images/results/{job_id}` | status, tiers, `total_listings` (listings, not unique products), `discovered`, `in_database`, `with_price`, paginated rows; `keywords=` ranks matching titles first (no API call) |
| `POST /api/search/images/results/{job_id}/verify` | read the price on the product page (public JSON-LD) → `price_verified` |
| `POST /api/search/images/results/{job_id}/save` | discovered result → SpyAds (listing source `google_lens`, country `WW` unless the page says otherwise) + picture into the local index |
| `GET /api/search/images/quota` | budgets, used today / month, cache hits, errors, refused, discovered results, real SerpApi usage (Account API, free, cached 10 min) |
| `POST /api/products/compare-prices-by-image` + `lens_job_id` | local offers + priced web results in one table (web price = "observed in search results" until verified) |

**Quota rules (hard, backend).** One Lens request per new (image sha256, crop, country, language, type, extra query);
same key → cache (`IMAGE_SEARCH_CACHE_TTL_HOURS`, default 168 h); concurrent identical requests join one job
(advisory lock = single-flight); budget reserved under a cross-process lock before the call; over budget → `refused`
row, no call, cache + local keep working; no scheduler use, no auto refresh; 401 / 403 / 429 / timeout not retried;
the API key never leaves the backend and is scrubbed from every stored error. Measured: **an image upload does not count
toward SerpApi usage** (Account API: 1 upload + 1 search = 1), so a new uploaded picture costs exactly 1 search.

**Normalisation.** `exact_matches` + `visual_matches` → one list deduped by product URL (exact wins); both price shapes
(`price{value,extracted_value,currency}` and `price`+`extracted_price`); currency only when unambiguous (`$` needs the
search country; `kr` needs SE/DK/NO) — otherwise the number is kept with "tiền tệ chưa rõ"; country stays null.
`position` is never used as a similarity: the first `IMAGE_SEARCH_VERIFY_TOP` (40) result thumbnails are downloaded
(SSRF-guarded) and measured against the query with the local engine (hash distance + CLIP cosine) → EXACT / LIKELY_SAME
(Lens exact match + visual agreement, or cosine ≥ 0.95) / VISUALLY_SIMILAR.

**Live test results (real SerpApi, not mocked)** — picture: Wikimedia "AirPods Pro (2nd generation).jpg", not in the DB.

| run | result |
|---|---|
| local only | 9–10 look-alikes from the index (no EXACT) |
| live, `country=de` | 56 listings in 7.6 s, 6 with real EUR prices (MediaMarkt 149 / 269, Saturn 272, OTTO 234, Amazon.de 139 refurb., Refurbed 167), each row with its own shop thumbnail; all graded VISUALLY_SIMILAR (AirPods Pro 1 / 2 / 3 mixed → correctly not same-SKU) |
| same search again | cache, 0 API calls |
| verify price on MediaMarkt | HTTP 403 → `access_restricted`, price stays "Unverified" (no bypass) |
| save result | listing #2 created, picture indexed |
| live from the UI, no country | 58 listings in 10.8 s, 3 copies of the exact photo graded EXACT 100 %; **0 prices** — Lens returns shop prices only when a country is given (UI now says so) |
| reopen the same picture | cache only, no call |
| quota after all tests | internal 2 / SerpApi Account API 2 (`plan_searches_left` 248/250) |

Offline (`test_lens.py`, **mocked SerpApi** — plumbing only): currency rules, normalisation, cache, single-flight,
daily budget refusal, missing key, 401 / timeout without retry, key redaction, API surface incl. save / compare.

Limits: SerpApi Free = 250 searches / month (`IMAGE_SEARCH_MONTHLY_BUDGET=250`, `DAILY=8` by default); many shop pages
answer 403 to price verification; a request cannot be cancelled once sent (quota already spent); results are "listings"
(articles, videos and shops mixed) — use "Chỉ listing có giá".

How to get a key: sign up at https://serpapi.com/users/sign_up (free plan, no card), copy the key from
https://serpapi.com/manage-api-key into `SERPAPI_API_KEY` in the root `.env`, restart the API.

## pgvector (installed 2026-10-09)

pgvector 0.8.6 is installed in PG 18 (`D:\PostgreSQL\18`) from the community Windows build
`andreiramani/pgvector_pgsql_windows` (pgvector has no official Windows binaries); `scripts/install_pgvector.ps1`
(elevated) pins the reviewed `vector.dll` SHA256 `800c2ee5…ec35`. `python -m app.visual.migrate_pgvector` added
`product_images.embedding_vec vector(512)`, copied the BYTEA vectors (60 169 in 47 s) and built an HNSW cosine index (9 s).
`embed.pgvector_ready()` switches search to `ORDER BY embedding_vec <=> q` with `hnsw.ef_search=300` and mirrors new
embeddings into both columns; BYTEA stays as source of truth and fallback (SQLite / no extension → numpy path).
HNSW recall vs exact numpy on the same rows: 98 % of the ≥0.85 hits at ef_search 300 (≈14 ms / query); rerun the migration
(idempotent) after any backfill that ran before the extension existed. Status shows `vector_search: "pgvector (HNSW)"`.

## Done / not done / blocked

**Done:** P0 audit · P1 hash search + UI everywhere · P2 CLIP embeddings (engine, worker, query) · P3 price comparison
by image, similar vs same-SKU split, reviews, watch-by-picture alerts · P4 public-data-first EU (JSON-LD live, honest statuses) ·

**Not done / open:**
- CLIP ground-truth numbers (`eval_image_search.py 150 --write` after the backfill) and threshold confirmation.
- (pgvector: done, see above.)
- External image search APIs (1688 image search, Google Lens…): none with verified access → internal index only, labelled "tìm trong kho SpyAds, không tìm live".
- Allegro / eMAG / Cdiscount etc.: `access_restricted` (seller / partner APIs) — by design.
- Load test with concurrent users; memory-leak soak (manual check of RSS after idle unload only).
- `ProductMatch` auto rules beyond user review (only EXACT-by-sha is implied; nothing auto-confirms LIKELY_SAME).

## Ops

Env (root `.env`, all optional): `IMAGE_INDEX_EVERY_MIN=1` (0 = never index in this worker), `IMAGE_INDEX_BATCH=400`,
`IMAGE_INDEX_EU_BATCH=40`, `IMAGE_EMBED_ENABLED=1`, `IMAGE_EMBED_MODEL=fp32|int8`, `IMAGE_MODEL_DIR`, `IMAGE_EMBED_THREADS=4`,
`IMAGE_EMBED_BATCH=16`, `IMAGE_EMBED_PER_TICK=400`, `IMAGE_EMBED_IDLE_S=600`, `IMAGE_EXACT_DIST=6`, `IMAGE_SIMILAR_DIST=14`,
`IMAGE_SAME_COS=0.95`, `IMAGE_SIMILAR_COS=0.80`, `IMAGE_SEARCH_MAX_MB=8`, `IMAGE_SEARCH_MAX_PIXELS=30000000`,
`IMAGE_FETCH_TIMEOUT_S=10`.

- Model files: `apps/api/data/models/clip-vit-b32/vision_model.onnx` (+ `_quantized.onnx`) from
  `https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/…` (gitignored under `data/`). Without them the
  app runs hash-only and the UI shows "CLIP tắt".
- Restart both processes after backend edits (`start.ps1`, or the ToolSpyAPI / ToolSpyWorker tasks). The worker indexes new
  thumbnails within a minute; a one-off full backfill: `python -c "from app.visual.service import index_pending; print(index_pending(100000, 0))"`
  and `python -c "from app.visual.embed import embed_pending; print(embed_pending(100000))"` (loop until 0).
- Status: `GET /api/search/images/status`. Evaluation: `cd apps/api && .venv/Scripts/python eval_image_search.py 150 --write`.
- pgvector later: build/install the extension for PG 18, run `docs/sql/pgvector_migration.sql`, then switch `EmbIndex.topk`.
