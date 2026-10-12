"""Global image search (spec "GLOBAL IMAGE SEARCH & EU MARKET REBUILD", docs/IMAGE_SEARCH.md).

Tier 1 = exact / near-duplicate by sha256 + pHash + dHash (ImageHash); tier 2 = visual similarity by CLIP embedding
(embed.py, optional — the hash tier works without it). One index table (`product_images`) points back at the owning
row (creative / EU listing); indexing runs in the worker, a search only hashes + embeds the query image.
"""
