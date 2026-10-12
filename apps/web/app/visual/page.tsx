"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, Eye, Globe, Search } from "lucide-react";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { takeStashedImage } from "@/components/visual/global-image-search";
import { CropBox, ImageCropDialog, ImageInput, ImagePreview, ImageUploadDropzone, usePasteImage } from "@/components/visual/image-input";
import { DEFAULT_FILTERS, Filters, ImageSearchProgress, IndexCoverage, PriceComparisonTable, ProductImageCard, SimilarAdsPanel, VisualSearchFilters } from "@/components/visual/results";
import { QuotaPanel, WebResultCard } from "@/components/visual/web-results";
import { WatchImageDialog } from "@/components/visual/watch-dialog";
import { api, useApi } from "@/lib/api";

type Origin = "hybrid" | "local" | "web";
const ORIGINS: [Origin, string, string][] = [
  ["hybrid", "Hybrid", "Tìm trong kho ngay, rồi tìm trên Internet (Google Lens) — tìm được cả sản phẩm chưa có trong kho"],
  ["local", "Chỉ trong kho", "Không tốn lượt API"],
  ["web", "Chỉ Internet", "Google Lens qua SerpApi — 1 lượt cho mỗi ảnh mới (ảnh đã tìm thì dùng cache)"],
];
const WEB_COUNTRIES = ["", "PH", "US", "GB", "DE", "FR", "IT", "ES", "NL", "BE", "PL", "AU", "AE", "SA", "VN"];
const STRONG = ["EXACT", "LIKELY_SAME_PRODUCT"];
const LIVE_ERR: Record<string, string> = {
  missing_api_key: "Chưa cấu hình SERPAPI_API_KEY trên server — tìm trong kho vẫn hoạt động.", quota_exceeded_daily: "Đã hết ngân sách tìm Internet hôm nay (IMAGE_SEARCH_DAILY_BUDGET).",
  quota_exceeded_monthly: "Đã hết ngân sách tìm Internet tháng này (IMAGE_SEARCH_MONTHLY_BUDGET).", unauthorized: "SerpApi từ chối API key (401).",
  rate_limited: "SerpApi đang giới hạn tốc độ (429) — thử lại sau.", timeout: "SerpApi không trả lời kịp.", provider_quota_exhausted: "Tài khoản SerpApi đã hết lượt.",
  live_search_disabled: "Tìm Internet đang tắt (IMAGE_SEARCH_LIVE_ENABLED=false).",
};

function VisualPage() {
  const sp = useSearchParams();
  const router = useRouter();
  const [input, setInput] = useState<ImageInput | null>(null);
  const [crop, setCrop] = useState<CropBox | null>(null);
  const [cropOpen, setCropOpen] = useState(false);
  const [origin, setOrigin] = useState<Origin>(((sp.get("origin") as Origin) || "hybrid"));
  const [webCountry, setWebCountry] = useState((sp.get("country") || "").toUpperCase());
  const [lensQ, setLensQ] = useState("");
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS, mode: sp.get("mode") || "visual", sources: (sp.get("sources") || "").split(",").filter(Boolean),
                                                     keywords: (sp.get("keywords") || "").split(/[|,]/).map((s) => s.trim()).filter(Boolean), country: "" });
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<any>(null);         // local search
  const [web, setWeb] = useState<any>(null);           // live job view
  const [webMsg, setWebMsg] = useState<{ code: string; text: string } | null>(null);
  const [prices, setPrices] = useState<any>(null);
  const [pricesBusy, setPricesBusy] = useState(false);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState(sp.get("mode") === "compare_prices" ? "prices" : "strong");
  const [pricedOnly, setPricedOnly] = useState(false);
  const [watchOpen, setWatchOpen] = useState(false);
  const ref = useRef<string | null>(null);              // image_ref of the uploaded file (one upload, many searches)
  const poll = useRef<any>(null);
  const status = useApi("/search/images/status");
  const quota = useApi("/search/images/quota");

  useEffect(() => {
    const ids = ["creative_id", "ad_id", "product_id", "eu_listing_id"].filter((k) => sp.get(k));
    if (ids.length) {
      const e: any = { kind: "entity" };
      ids.forEach((k) => (e[k] = Number(sp.get(k))));
      e.label = ids.map((k) => `${k.replace("_id", "")} #${sp.get(k)}`).join(", ");
      e.preview = e.creative_id ? `/api/creatives/${e.creative_id}/thumb` : null;
      setInput(e);
    } else if (sp.get("image_url")) {
      setInput({ kind: "url", url: sp.get("image_url")!, preview: sp.get("image_url")! });
    } else {
      const f = takeStashedImage();
      if (f) setInput({ kind: "file", file: f, preview: URL.createObjectURL(f) });
    }
  }, [sp]);
  usePasteImage(useCallback((f: File) => { ref.current = null; setInput({ kind: "file", file: f, preview: URL.createObjectURL(f) }); setCrop(null); }, []));
  useEffect(() => () => clearInterval(poll.current), []);

  const reviewFor = useMemo(() => input?.kind === "entity" && input.product_id ? { source: "product", source_id: input.product_id } : null, [input]);

  /** FormData describing the picture: image_ref (after one upload) | url | entity ids, + crop. */
  const imageForm = async (inp: ImageInput, c: CropBox | null) => {
    const fd = new FormData();
    if (inp.kind === "file") {
      if (!ref.current) {
        const up = new FormData(); up.append("file", inp.file);
        ref.current = (await api("/search/images/upload", { method: "POST", body: up })).image_ref;
      }
      fd.append("image_ref", ref.current!);
    } else if (inp.kind === "url") fd.append("image_url", inp.url);
    else ["creative_id", "ad_id", "product_id", "eu_listing_id"].forEach((k) => (inp as any)[k] && fd.append(k, String((inp as any)[k])));
    if (c) fd.append("crop", `${c.x},${c.y},${c.w},${c.h}`);
    return fd;
  };

  const followJob = (jobId: number, cached: boolean, kws: string[]) => new Promise<any>((resolve) => {
    clearInterval(poll.current);
    const load = async () => {
      const v = await api(`/search/images/results/${jobId}?limit=200${kws.length ? `&keywords=${encodeURIComponent(kws.join("|"))}` : ""}`);
      setWeb({ ...v, cached });
      if (v.status !== "running") {
        clearInterval(poll.current);
        if (v.status === "error") setWebMsg({ code: v.error_code, text: LIVE_ERR[v.error_code] || `${v.error_code}: ${v.error || ""}` });
        quota.reload();
        resolve(v);
      }
    };
    load();
    let n = 0;
    poll.current = setInterval(() => { if (++n > 80) { clearInterval(poll.current); resolve(null); } else load(); }, 1500);
  });

  const runWeb = async (inp: ImageInput, c: CropBox | null, f: Filters, cacheOnly: boolean) => {
    setWebMsg(null);
    const fd = await imageForm(inp, c);
    if (webCountry) fd.append("country", webCountry);
    if (lensQ.trim()) fd.append("lens_q", lensQ.trim());
    if (cacheOnly) fd.append("cache_only", "true");
    try {
      const j = await api("/search/images/live", { method: "POST", body: fd });
      if (j.status === "not_cached") { setWeb(null); setWebMsg({ code: "not_cached", text: "Ảnh này chưa được tìm trên Internet với các tham số này." }); return null; }
      return await followJob(j.job_id, j.cached, f.keywords);
    } catch (e: any) {
      const m = String(e.message || e);
      const code = Object.keys(LIVE_ERR).find((k) => m.includes(k)) || "error";
      setWebMsg({ code, text: LIVE_ERR[code] || m });
      return null;
    }
  };

  const loadPrices = async (inp: ImageInput, c: CropBox | null, f: Filters, jobId?: number | null) => {
    setPricesBusy(true);
    try {
      const fd = await imageForm(inp, c);
      if (f.keywords.length) fd.append("keywords", f.keywords.join("|"));
      if (jobId) fd.append("lens_job_id", String(jobId));
      setPrices(await api("/products/compare-prices-by-image", { method: "POST", body: fd }));
    } catch (e: any) { setErr(String(e.message || e)); } finally { setPricesBusy(false); }
  };

  /** auto = page opened with a picture (from a link / stash): the web part only reads the cache — opening a page
   *  never spends API quota. An explicit "Tìm" runs the live search when the cache has nothing. */
  const run = useCallback(async (f: Filters, inp: ImageInput | null, c: CropBox | null, auto = false) => {
    if (!inp) return;
    setBusy(true); setErr(""); setData(null); setWeb(null); setPrices(null); setWebMsg(null);
    try {
      const local = origin !== "web" ? (async () => {
        const fd = await imageForm(inp, c);
        fd.append("mode", f.mode === "compare_prices" ? "visual" : f.mode);
        if (f.keywords.length) fd.append("keywords", f.keywords.join("|"));
        if (f.country) fd.append("country", f.country);
        if (f.sources.length) fd.append("sources", f.sources.join(","));
        if (f.price_min) fd.append("price_min", f.price_min);
        if (f.price_max) fd.append("price_max", f.price_max);
        fd.append("sort", f.sort); fd.append("limit", "120");
        setData(await api("/search/images", { method: "POST", body: fd }));
      })() : Promise.resolve();
      const remote = origin !== "local" ? runWeb(inp, c, f, auto) : Promise.resolve(null);
      await local;
      setBusy(false);  // local results are on screen; the web part keeps going
      const w = await remote;
      if (f.mode === "compare_prices") loadPrices(inp, c, f, w?.status === "ok" ? w.job_id : null);
    } catch (e: any) {
      const m = String(e.message || e);
      setErr(m.includes("rate_limited") ? "Quá nhiều lượt tìm ảnh — đợi một phút." : m);
    } finally { setBusy(false); }
  }, [origin, webCountry, lensQ]);  // eslint-disable-line react-hooks/exhaustive-deps

  const [auto, setAuto] = useState("");
  useEffect(() => {
    const key = input ? JSON.stringify(input.kind === "file" ? input.file.name + input.file.size : input) : "";
    if (input && key !== auto) { setAuto(key); run(filters, input, null, true); }
  }, [input]);  // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (i: ImageInput) => { ref.current = null; setInput(i); setCrop(null); if (sp.toString()) router.replace("/visual"); };
  const apply = (f: Filters) => { setFilters(f); if (f.mode === "compare_prices") setTab("prices"); if (input && !busy) run(f, input, crop); };
  const change = (f: Filters) => {
    const instant = f.mode !== filters.mode || f.sort !== filters.sort || f.country !== filters.country || f.sources.join() !== filters.sources.join();
    instant ? apply(f) : setFilters(f);
  };

  const localRows: any[] = data?.rows || [];
  const webRows: any[] = (web?.rows || []).map((r: any, i: number) => ({ ...r, _i: r.index ?? i }));  // _i = stored index: what verify / save act on
  const updWeb = (i: number, row: any) => setWeb((w: any) => w && { ...w, rows: w.rows.map((r: any, j: number) => ((r.index ?? j) === i ? { ...row, index: i } : r)) });
  const strongLocal = localRows.filter((r) => STRONG.includes(r.tier)), strongWeb = webRows.filter((r) => STRONG.includes(r.tier));
  const simLocal = localRows.filter((r) => !STRONG.includes(r.tier)), simWeb = webRows.filter((r) => !STRONG.includes(r.tier));
  const discovered = webRows.filter((r) => !r.in_database && (!pricedOnly || r.price != null));
  const webCard = (r: any) => <WebResultCard key={`w${r._i}`} r={r} jobId={web.job_id} index={r._i} cached={!!web.cached} onChanged={(row) => updWeb(r._i, row)} />;
  const grid = (cards: React.ReactNode[]) => <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">{cards}</div>;
  const webRunning = web?.status === "running";
  const result = prices ? { query: prices.query, rows: [...(prices.same_product?.rows || []), ...(prices.similar_products?.rows || [])] } : data || (web ? { query: null, rows: [] } : null);
  useEffect(() => { if (sp.get("watch") && data?.query) setWatchOpen(true); }, [data]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4">
      <PageHeader title="Tìm bằng ảnh" subtitle="Một ảnh bất kỳ — kể cả chưa từng có trong SpyAds: tìm trong kho (ads, nguồn hàng, EU) và trên Internet (Google Lens), xem shop, giá, link thật; so sánh giá; lưu sản phẩm mới vào kho.">
        <IndexCoverage st={status.data} />
      </PageHeader>
      <QuotaPanel q={quota.data} onRefresh={quota.reload} />
      <Card>
        <div className="grid gap-4 lg:grid-cols-[minmax(260px,1fr)_2fr]">
          <div>
            {input ? <ImagePreview input={input} crop={crop} onCrop={input.preview ? () => setCropOpen(true) : undefined}
                                   onClear={() => { ref.current = null; setInput(null); setData(null); setWeb(null); setPrices(null); setCrop(null); }} />
                   : <ImageUploadDropzone onPick={pick} compact />}
            {input && <ImageUploadDropzone onPick={pick} compact className="mt-3 opacity-80" />}
          </div>
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={origin} onValueChange={(v) => v && setOrigin(v as Origin)}>
                {ORIGINS.map(([k, l, hint]) => <ToggleGroupItem key={k} value={k} className="text-xs" title={hint} aria-label={l}>{k === "web" || k === "hybrid" ? <Globe className="size-3.5" /> : null}{l}</ToggleGroupItem>)}
              </ToggleGroup>
              {origin !== "local" && <>
                <SimpleSelect value={webCountry} onChange={setWebCountry} options={WEB_COUNTRIES.filter(Boolean)} placeholder="Google: mặc định" className="w-40 text-xs" />
                <Input className="h-8 w-56 text-xs" placeholder="Từ khoá gửi kèm Lens (tuỳ chọn)" value={lensQ} onChange={(e) => setLensQ(e.target.value)}
                       title="Gửi kèm ảnh tới Google Lens (tham số q). Đổi từ khoá = một lượt tìm mới. Từ khoá ở ô bên dưới chỉ lọc / xếp hạng, không tốn lượt." />
              </>}
            </div>
            <VisualSearchFilters value={filters} onChange={change} disabled={busy} />
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" onClick={() => apply(filters)} disabled={!input || busy || webRunning}>{busy || webRunning ? <><Spinner /> Đang tìm…</> : <><Search /> Tìm</>}</Button>
              {data && <span className="text-xs text-muted-foreground">Trong kho: {data.total} kết quả ({data.took_ms} ms, {data.query?.embedding ? "hash + CLIP" : "hash"})</span>}
              {web && <span className="text-xs text-muted-foreground">· Internet: {web.status === "running" ? "đang tìm…" : `${web.total_listings} listing (${web.cached ? "cache" : "live"}, ${web.with_price} có giá, ${web.discovered} mới)`}</span>}
              {result && <Button type="button" variant="outline" size="sm" onClick={() => setWatchOpen(true)} disabled={!data?.query}><Eye /> Theo dõi</Button>}
            </div>
            {webMsg && (webMsg.code === "not_cached"
              ? <div className="flex flex-wrap items-center gap-2 text-xs"><span className="text-muted-foreground">{webMsg.text}</span>
                  <Button size="xs" variant="secondary" onClick={() => input && runWeb(input, crop, filters, false)}><Globe /> Tìm trên Internet (1 lượt)</Button></div>
              : <p className="text-xs text-warning">Internet: {webMsg.text} Kết quả trong kho vẫn hiển thị.</p>)}
            {data?.saved_listing && <p className="text-xs text-good-text">Đã lưu listing từ trang sản phẩm vào SpyAds: “{data.saved_listing.title}” (#{data.saved_listing.eu_listing_id})</p>}
          </div>
        </div>
      </Card>
      {input?.preview && <ImageCropDialog src={input.preview} open={cropOpen} onOpenChange={setCropOpen} value={crop} onChange={(c) => { setCrop(c); if (input) run(filters, input, c); }} />}
      <WatchImageDialog open={watchOpen} onOpenChange={setWatchOpen} query={data?.query || null} rows={result?.rows || []} keywords={filters.keywords} thumb={input?.kind === "entity" ? input.preview : null} />
      {err && <Alert variant="destructive"><AlertCircle /><AlertTitle>Không tìm được</AlertTitle><AlertDescription>{err}</AlertDescription></Alert>}
      {busy ? <ImageSearchProgress /> : status.error ? <Loading error={status.error} /> : !input ? (
        <Card><p className="text-sm text-muted-foreground">Chọn một tấm ảnh. Ví dụ: ảnh vòng tay phong thuỷ + từ khoá “lucky bracelet, feng shui bracelet” → kết quả trong kho ngay, rồi bấm Tìm để khám phá trên Internet (shop, giá, link).</p></Card>
      ) : (data || web || prices) && (
        <Tabs value={tab} onValueChange={(v) => { setTab(v); if (v === "prices" && !prices && input) loadPrices(input, crop, filters, web?.status === "ok" ? web.job_id : null); }}>
          <TabsList className="flex-wrap">
            <TabsTrigger value="strong">Trùng & có thể cùng SP ({strongLocal.length + strongWeb.length})</TabsTrigger>
            <TabsTrigger value="similar">Sản phẩm tương tự ({simLocal.length + simWeb.length})</TabsTrigger>
            <TabsTrigger value="discovered">Khám phá trên Internet ({webRunning ? "…" : web ? web.discovered : 0})</TabsTrigger>
            <TabsTrigger value="prices">So sánh giá</TabsTrigger>
          </TabsList>
          <TabsContent value="strong" className="space-y-4">
            {strongLocal.length > 0 && <SimilarAdsPanel rows={strongLocal} />}
            {strongLocal.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Trong kho SpyAds</h3>{grid(strongLocal.map((r) => <ProductImageCard key={r.key} r={r} reviewFor={reviewFor} />))}</section>}
            {strongWeb.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Trên Internet</h3>{grid(strongWeb.map(webCard))}</section>}
            {!strongLocal.length && !strongWeb.length && <Card><Empty title="Chưa có ảnh trùng / có thể cùng sản phẩm">{webRunning ? "Đang tìm trên Internet…" : "Xem tab Sản phẩm tương tự và Khám phá trên Internet. Không khẳng định cùng SKU chỉ vì ảnh giống."}</Empty></Card>}
          </TabsContent>
          <TabsContent value="similar" className="space-y-4">
            {simLocal.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Trong kho SpyAds ({simLocal.length})</h3>{grid(simLocal.map((r) => <ProductImageCard key={r.key} r={r} reviewFor={reviewFor} />))}</section>}
            {simWeb.length > 0 && <section><h3 className="mb-2 text-sm font-semibold">Trên Internet ({simWeb.length})</h3>{grid(simWeb.map(webCard))}</section>}
            {!simLocal.length && !simWeb.length && <Card><Empty title="Không có kết quả tương tự">{webRunning ? "Đang tìm trên Internet…" : "Không có ảnh tương tự trong kho; thử tìm trên Internet."}</Empty></Card>}
          </TabsContent>
          <TabsContent value="discovered" className="space-y-3">
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span>{web ? `${web.total_listings} listing tìm thấy (không phải số sản phẩm duy nhất) · ${web.in_database} đã có trong kho · tìm lúc ${new Date(web.finished_at + "Z").toLocaleString("vi-VN")}` : "Chưa tìm trên Internet."}</span>
              <Label className="text-xs"><Checkbox checked={pricedOnly} onCheckedChange={(v) => setPricedOnly(v === true)} /> Chỉ listing có giá</Label>
            </div>
            {web?.status === "ok" && web.with_price === 0 && !web.params?.country && (
              <p className="text-xs text-warning">Google Lens chỉ trả giá shop khi có quốc gia: chọn “Google: DE / US / PH…” ở trên rồi bấm Tìm (1 lượt mới cho mỗi quốc gia).</p>
            )}
            {webRunning ? <ImageSearchProgress label="Google Lens đang tìm trên Internet…" /> : discovered.length ? grid(discovered.map(webCard))
              : <Card><Empty title="Chưa có sản phẩm mới">{webMsg?.text || "Bấm Tìm (Hybrid hoặc Chỉ Internet) để khám phá sản phẩm ngoài kho."}</Empty></Card>}
          </TabsContent>
          <TabsContent value="prices">
            {pricesBusy ? <ImageSearchProgress label="Đang gom offer có giá (kho + kết quả Internet đã có)…" /> : prices ? (
              <div className="space-y-2">
                {prices.web ? <p className="text-xs text-muted-foreground">Gồm {prices.web.priced} listing có giá từ Internet (giá quan sát trong kết quả tìm kiếm, chưa chắc là giá hiện tại — bấm “Xác minh giá” ở thẻ kết quả để đọc giá trên trang).</p>
                  : origin !== "local" && <p className="text-xs text-muted-foreground">Chưa có kết quả Internet cho ảnh này — bảng chỉ gồm dữ liệu trong kho.</p>}
                <PriceComparisonTable data={prices} />
              </div>
            ) : <Card><Empty title="So sánh giá">Mở tab để gom offer có giá.</Empty></Card>}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><VisualPage /></Suspense>;
}
