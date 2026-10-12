"use client";
import { useEffect, useRef, useState } from "react";
import { RefreshCw, Search } from "lucide-react";
import { LoadMore } from "@/components/paging";
import { SimpleSelect } from "@/components/simple-select";
import { Card, PageHeader } from "@/components/ui";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { CountryChips, EuLoading, NoData, ProductGrid, SOURCE_LABEL } from "@/components/eu-market/shared";
import { api, qs, usePaged } from "@/lib/api";
import { GlobalImageSearch } from "@/components/visual/global-image-search";
import { AllegroBotSearch } from "@/components/eu-market/allegro-bot";

const SORTS: [string, string][] = [["recent", "Mới cập nhật"], ["price_asc", "Giá thấp → cao (EUR)"], ["price_desc", "Giá cao → thấp (EUR)"], ["offers", "Nhiều offer nhất"]];

export default function EuProductExplorer() {
  const [kw, setKw] = useState("");
  const [countries, setCountries] = useState<string[]>([]);
  const [source, setSource] = useState("");
  const [kind, setKind] = useState("");
  const [sort, setSort] = useState("recent");
  const [more, setMore] = useState({ brand: "", seller: "", category: "", price_min: "", price_max: "", days: "", has_ads: false });
  const [applied, setApplied] = useState<any>({});
  const [live, setLive] = useState<{ busy: boolean; msg: string; bad?: boolean } | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);

  const apply = () => setApplied({ keywords: kw.split(/\n/).map((s) => s.trim()).filter(Boolean).join(" | "), countries: countries.join(","),
                                   sources: source, kind, sort, ...more, has_ads: more.has_ads || undefined });
  useEffect(apply, [countries, source, kind, sort]); // eslint-disable-line react-hooks/exhaustive-deps
  const { rows, total, last, loading, error, hasMore, loadMore } = usePaged(`/eu-market/products${qs({ ...applied, t: tick })}`, 24);
  const [pageUrl, setPageUrl] = useState("");
  const [urlMsg, setUrlMsg] = useState<{ ok: boolean; text: string } | null>(null);
  /** Public product page → listing via its JSON-LD (no login, no anti-bot): spec §6.2 (4). */
  async function addFromUrl() {
    const u = pageUrl.trim();
    if (!/^https?:\/\//i.test(u)) return setUrlMsg({ ok: false, text: "Dán URL trang sản phẩm http(s)." });
    setUrlMsg({ ok: true, text: "Đang đọc trang…" });
    try {
      const r = await api("/eu-market/listings/from-url", { method: "POST", body: JSON.stringify({ url: u }) });
      const l = r.listing;
      setUrlMsg({ ok: true, text: `Đã lưu: ${l.title} · ${l.price != null ? `${l.price} ${l.currency}` : "giá: unknown"} · ${l.market_country}${r.image_indexed ? " · ảnh đã vào chỉ mục" : ""}` });
      setPageUrl(""); setTick((t) => t + 1);
    } catch (e: any) { setUrlMsg({ ok: false, text: String(e.message || e) }); }
  }

  /** Live scan of the configured APIs (admin/sync runs in the background), then poll sync-runs until done. */
  async function scan() {
    const keywords = kw.split(/[\n|,]/).map((s) => s.trim()).filter(Boolean);
    if (!keywords.length) return setLive({ busy: false, msg: "Nhập ít nhất một từ khoá.", bad: true });
    setLive({ busy: true, msg: "Đang gửi yêu cầu…" });
    try {
      const r = await api("/eu-market/admin/sync", { method: "POST", body: JSON.stringify({ keywords, countries, sources: source ? [source] : [] }) });
      const probs = (r.problems || []).map((p: any) => `${SOURCE_LABEL[p.source] || p.source}: ${p.error}${p.detail ? ` (${p.detail})` : ""}`).join(" · ");
      if (!r.started) return setLive({ busy: false, bad: true, msg: `Không có nguồn nào quét được. ${probs}` });
      apply();
      const t0 = Date.now();
      for (;;) {
        await new Promise((res) => setTimeout(res, 2500));
        if (!alive.current) return;
        const s = await api(`/eu-market/admin/sync-runs${qs({ since: r.started_at, limit: 200 })}`);
        const done = s.items.filter((x: any) => x.status !== "running");
        const written = done.reduce((a: number, x: any) => a + (x.items_written || 0), 0);
        const errs = done.filter((x: any) => x.status !== "ok").map((x: any) => `${x.connector} ${x.country}: ${x.error_code}`);
        setTick((t) => t + 1);
        const finished = s.items.length > 0 && s.running === 0;
        setLive({ busy: !finished, bad: finished && !written && errs.length > 0,
                  msg: `${finished ? "Xong" : "Đang quét"} ${r.sources.map((x: string) => SOURCE_LABEL[x] || x).join(", ")} · ${done.length}/${s.items.length || "…"} lượt · ${written} listing${errs.length ? ` · lỗi: ${errs.slice(0, 4).join(", ")}` : ""}${probs ? ` · bỏ qua: ${probs}` : ""}` });
        if (finished || Date.now() - t0 > 180_000) break;
      }
    } catch (e: any) {
      setLive({ busy: false, bad: true, msg: String(e.message || e) });
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Product Explorer" subtitle="Nhiều từ khoá (mỗi dòng một từ đồng nghĩa / ngôn ngữ) = OR; quốc gia, giá, danh mục = AND. Không dịch máy, không gộp sản phẩm khác nhau chỉ vì cùng từ khoá. Đây là listing marketplace (Allegro, Kaufland…), không phải dữ liệu quảng cáo.">
        <GlobalImageSearch preset={{ sources: "eu_listing", mode: "similar_product" }} />
      </PageHeader>
      <Card>
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <div className="space-y-2">
            <Textarea rows={3} value={kw} onChange={(e) => setKw(e.target.value)} placeholder={"feng shui bracelet\nlucky bracelet\nbracelet porte-bonheur\nGlücksarmband"}
                      aria-label="Từ khoá" className="text-sm" />
            <CountryChips value={countries} onChange={setCountries} />
            <div className="flex gap-1.5">
              <Input value={pageUrl} onChange={(e) => setPageUrl(e.target.value)} placeholder="…hoặc dán URL trang sản phẩm công khai (JSON-LD) để thêm listing + ảnh" className="h-8 text-xs"
                     onKeyDown={(e) => e.key === "Enter" && addFromUrl()} aria-label="URL trang sản phẩm" />
              <Button type="button" variant="outline" size="sm" onClick={addFromUrl} disabled={!pageUrl.trim()}>Thêm từ URL</Button>
            </div>
            {urlMsg && <p className={`text-xs ${urlMsg.ok ? "text-muted-foreground" : "text-critical"}`}>{urlMsg.text}</p>}
          </div>
          <div className="flex flex-col gap-2 lg:w-56">
            <Button onClick={apply}><Search />Tìm trong kho</Button>
            <Button variant="outline" onClick={scan} disabled={live?.busy}>{live?.busy ? <Spinner /> : <RefreshCw />}Quét nguồn (live)</Button>
            <SimpleSelect value={source} onChange={setSource} placeholder="Mọi nguồn" options={(["jsonld", "allegro", "kaufland", "bol", "bigbuy"] as const).map((k) => [k, SOURCE_LABEL[k]] as [string, string])} />
            <SimpleSelect value={kind} onChange={setKind} placeholder="Bán lẻ + B2B" options={[["marketplace_listing", "Chỉ listing bán lẻ"], ["supplier_offer", "Chỉ nguồn nhập B2B"]]} />
            <SimpleSelect value={sort} onChange={setSort} options={SORTS} />
          </div>
        </div>
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-muted-foreground">Bộ lọc thêm</summary>
          <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-7">
            {(["brand", "seller", "category"] as const).map((k) => (
              <Input key={k} placeholder={{ brand: "Thương hiệu", seller: "Seller", category: "Danh mục" }[k]} value={more[k]} onChange={(e) => setMore({ ...more, [k]: e.target.value })} />
            ))}
            <Input type="number" min={0} placeholder="Giá từ" value={more.price_min} onChange={(e) => setMore({ ...more, price_min: e.target.value })} />
            <Input type="number" min={0} placeholder="Giá đến" value={more.price_max} onChange={(e) => setMore({ ...more, price_max: e.target.value })} />
            <Input type="number" min={1} placeholder="Cập nhật trong N ngày" value={more.days} onChange={(e) => setMore({ ...more, days: e.target.value })} />
            <Label className="flex items-center gap-2 text-sm"><Checkbox checked={more.has_ads} onCheckedChange={(v) => setMore({ ...more, has_ads: !!v })} />Có ad đã xác nhận</Label>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">Lọc giá theo giá gốc của listing (currency nguồn). Sắp xếp giá dùng giá quy đổi EUR theo tỷ giá ECB.</p>
        </details>
        {live && (
          <Alert variant={live.bad ? "destructive" : "default"} className="mt-3">
            <AlertDescription className="flex items-center gap-2">{live.busy && <Spinner />}{live.msg}</AlertDescription>
          </Alert>
        )}
      </Card>
      <AllegroBotSearch keyword={kw.split("\n")[0] || ""} onDone={() => setTick((t) => t + 1)} />
      {error ? <EuLoading error={error} /> : (
        <>
          <p className="text-sm text-muted-foreground">
            {total} sản phẩm{last?.keywords?.length ? ` cho: ${last.keywords.join(" · ")}` : ""}
            {last && !last.searchable_sources?.length && " · chưa nguồn nào có credentials để quét live"}
          </p>
          {rows.length ? <ProductGrid rows={rows} /> : !loading && <NoData>Không có listing khớp trong kho. Bấm “Quét nguồn (live)” nếu đã kết nối nguồn, hoặc xem</NoData>}
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
