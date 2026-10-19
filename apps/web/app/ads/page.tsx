"use client";
import { liveCountries, MarketPicker } from "@/components/MarketPicker";
import { useEffect, useState } from "react";
import { Search, Zap } from "lucide-react";
import { AdCard } from "@/components/AdCard";
import { LoadMore } from "@/components/paging";
import { Hint, NetworkChips, usePlatforms } from "@/components/platforms";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { api, qs, usePaged } from "@/lib/api";
import { getUser, useEvents, useTeam } from "@/lib/realtime";
import { AdvertiserFilters } from "@/components/workspace/actions";

const FUNNELS: [string, string][] = [["", "Tất cả"], ["mess", "💬 MKT Mess"], ["ladi", "🧾 MKT Ladi"], ["form", "Form"]];
const SORTS: [string, string][] = [["newest", "Mới chạy nhất"], ["longest", "Chạy lâu nhất"], ["variants", "Nhiều phiên bản nhất"], ["recent", "Mới thu thập"]];
const channel: string = "ads";  // listing nguồn hàng nằm ở menu "Nguồn hàng & Store" (/marketplace)

export default function AdsLibrary() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [country, setCountry] = useState("");
  const [funnel, setFunnel] = useState("");
  const [network, setNetwork] = useState("");
  const team = useTeam();
  useEffect(() => { setFunnel(team); }, [team]);
  const [media, setMedia] = useState("");
  const [active, setActive] = useState("true");
  const [sort, setSort] = useState("newest");
  const [strict, setStrict] = useState(false);  // keyword must be in title / product name / landing page, not just the story
  const [adId, setAdId] = useState("");
  useEffect(() => { const id = new URLSearchParams(location.search).get("ad_id"); if (id && /^\d+$/.test(id)) { setAdId(id); setActive(""); } }, []);
  const [scan, setScan] = useState("");  // live-scan status line
  const { networks, byKey } = usePlatforms();
  const path = `/ads${qs({ q: applied, country, funnel: funnel, media_type: media, active: active,
                          sort, channel, network, strict, ad_id: adId })}`;
  const { rows, total, last, loading, error, hasMore, loadMore, reload } = usePaged(path, 24);
  const scanLive = async () => {
    const kw = (q.trim() || applied).trim();
    if (!kw) return setScan("Nhập từ khoá trước");
    setApplied(kw); setScan("Đang quét các nguồn…");
    try {
      const { job_id } = await api("/search/live", { method: "POST", body: JSON.stringify({
        query: kw, countries: liveCountries(country), user: getUser().name, limit: 50, media_type: media || null }) });
      for (;;) {
        await new Promise((r) => setTimeout(r, 2500));
        const j = await api(`/search/jobs/${job_id}`);
        reload();
        if (j.status !== "running") return setScan(j.error && !j.found ? `Lỗi: ${j.error}` : `Xong: ${j.found} ads, ${j.new_ads} mới`);
        setScan(`Đang quét… ${j.found} ads`);
      }
    } catch (e: any) { setScan(`Không quét được: ${e.message}`); }
  };
  useEvents((e) => { if (e.type === "CREATIVE_STORED") reload(); });

  return (
    <div>
      <PageHeader title="Thư viện quảng cáo" subtitle="Mọi quảng cáo đã thu thập từ mọi nền tảng, mỗi thẻ là một quảng cáo thật kèm video/ảnh tải về được. TikTok Ads gồm Creative Center Top Ads (PH/US/EU/ME) và TikTok Ad Library (EU/UK)." />
      <Card className="mb-4">
        {adId && <Button variant="outline" size="sm" onClick={() => setAdId("")}>Đang xem Ads #{adId} · Xem tất cả</Button>}
        <AdvertiserFilters />
        <div className="flex flex-wrap items-center gap-2">
          <form className="flex min-w-[260px] flex-1 flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
            <div className="relative min-w-[180px] flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="h-8 pl-8" placeholder="Từ khoá trong nội dung / advertiser / landing page…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <Button size="sm" type="submit">Lọc</Button>
            <Hint tip="Quét trực tiếp Meta / TikTok… cho từ khoá này (tối đa 50 ads mỗi nước) rồi lưu vào kho">
              <Button variant="outline" size="sm" type="button" onClick={scanLive} disabled={scan.startsWith("Đang")}><Zap /> Quét nguồn</Button>
            </Hint>
          </form>
        </div>
        <div className="mt-3"><NetworkChips value={network} onChange={setNetwork} channel={channel} facets={last?.facets} networks={networks} /></div>
        <div className="mt-3"><MarketPicker value={country} onChange={setCountry} /></div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {channel === "ads" && (
            <ToggleGroup type="single" variant="outline" size="sm" value={funnel || "all"} onValueChange={(v) => v && setFunnel(v === "all" ? "" : v)}>
              {FUNNELS.map(([k, l]) => <ToggleGroupItem key={k || "all"} value={k || "all"} className="text-xs data-[state=on]:font-semibold">{l}</ToggleGroupItem>)}
            </ToggleGroup>
          )}
          <SimpleSelect value={media} onChange={setMedia} placeholder="Mọi loại media" className="w-36"
                        options={[["video", "Video"], ["image", "Ảnh"], ["carousel", "Carousel"], ["text", "Chữ (Search)"]]} />
          {channel === "ads" && (
            <SimpleSelect value={active} onChange={setActive} placeholder="Tất cả" className="w-32" options={[["true", "Đang chạy"], ["false", "Đã dừng"]]} />
          )}
          <div className="flex items-center gap-1.5" title="Chỉ giữ ads có từ khoá trong tiêu đề / tên sản phẩm / link landing page — bỏ ads chỉ nhắc từ khoá trong câu chuyện">
            <Checkbox id="ads-strict" checked={strict} onCheckedChange={(v) => setStrict(v === true)} />
            <Label htmlFor="ads-strict" className="text-xs font-normal">Chỉ ads đúng sản phẩm</Label>
          </div>
          <SimpleSelect value={sort} onChange={setSort} className="w-44" options={SORTS} />
          {scan && <span className="text-xs text-muted-foreground">{scan}</span>}
          <Badge variant="outline" className="tnum ml-auto">{total.toLocaleString("en-US")} quảng cáo</Badge>
        </div>
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? (
        <Card><Empty>Chưa có dữ liệu khớp bộ lọc. Vào <b>Tìm sản phẩm</b>, nhập từ khoá và bấm “Tìm trực tiếp trên nguồn” — app quét đồng thời mọi nguồn đang bật (Meta, TikTok, Snapchat, AliExpress).</Empty></Card>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 items-start">
            {rows.map((ad: any) => <AdCard key={ad.id} ad={ad} />)}
          </div>
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
