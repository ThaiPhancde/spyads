from app.collector.adapters.meta import _balanced, map_library_ad

html = 'x "search_results_connection":{"edges":[{"node":{"a":"}{\\"q"}}],"count":1} tail}'
assert _balanced(html, '"search_results_connection":') == '{"edges":[{"node":{"a":"}{\\"q"}}],"count":1}'
raw = {"ad_archive_id": "1", "page_id": "9", "page_name": "P", "fev_info": {"email": "a@b.c"},
       "snapshot": {"body": {"text": "hi"}, "images": [{"original_image_url": "http://i"}], "display_format": "IMAGE"}}
r = map_library_ad(raw, "meta_library", "US")
assert r.source_ad_id == "1" and r.ad_text == "hi" and r.raw_source["fev_info"]["email"] == "a@b.c"
print("ok")
