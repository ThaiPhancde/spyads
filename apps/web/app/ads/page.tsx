"use client";
import { MarketPicker } from "@/components/MarketPicker";
import { useEffect, useState } from "react";
import { AdCard } from "@/components/AdCard";
import { ListingCard } from "@/components/ListingCard";
import { LoadMore } from "@/components/paging";
import { NetworkChips, usePlatforms } from "@/components/platforms";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { qs, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const FUNNELS: [string, string][] = [["", "Tất cả"], ["mess", "💬 MKT Mess"], ["ladi", "🧾 MKT Ladi"], ["form", "Form"]];
const CHANNELS: [string, string, string][] = [
  ["ads", "Quảng cáo", "Ads thật từ các thư viện quảng cáo: Meta, TikTok, Snapchat"],
  ["commerce", "Nguồn hàng & store", "Nguồn hàng TQ (AliExpress, 1688, Taobao): giá nhập, đã bán"],
];
const SORTS: Record<string, [string, string][]> = {
  ads: [["newest", "Mới chạy nhất"], ["longest", "Chạy lâu nhất"], ["variants", "Nhiều phiên bản nhất"], ["recent", "Mới thu thập"]],
  commerce: [["sold", "Bán nhiều nhất"], ["reviews", "Nhiều đánh giá nhất"], ["price", "Giá thấp nhất"], ["recent", "Mới thu thập"]],
};

export default function AdsLibrary() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [country, setCountry] = useState("");
  const [funnel, setFunnel] = useState("");
  const [channel, setChannel] = useState("ads");
  const [network, setNetwork] = useState("");
  const team = useTeam();
  useEffect(() => { setFunnel(team); }, [team]);
  useEffect(() => { setNetwork(""); setSort(SORTS[channel][0][0]); }, [channel]);
  const [media, setMedia] = useState("");
  const [active, setActive] = useState("true");
  const [sort, setSort] = useState("newest");
  const { networks, byKey } = usePlatforms();
  const path = `/ads${qs({ q: applied, country, funnel: channel === "ads" ? funnel : "", media_type: media, active: channel === "ads" ? active : "",
                          sort, channel, network })}`;
  const { rows, total, last, loading, error, hasMore, loadMore, reload } = usePaged(path, 24);
  useEvents((e) => { if (e.type === "CREATIVE_STORED") reload(); });

  return (
    <div>
      <PageHeader title="Thư viện quảng cáo" subtitle="Mọi quảng cáo đã thu thập từ mọi nền tảng, mỗi thẻ là một quảng cáo thật kèm video/ảnh tải về được. TikTok Ads gồm Creative Center Top Ads (PH/US/EU/ME) và TikTok Ad Library (EU/UK); tab Nguồn hàng là listing TQ (AliExpress, 1688, Taobao)." />
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-2 pt-3">
          <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
            {CHANNELS.map(([k, l, tip]) => (
              <button key={k} title={tip} onClick={() => setChannel(k)} className="text-xs px-3 py-1.5"
                      style={{ background: channel === k ? "var(--series-1)" : "var(--surface-1)", color: channel === k ? "#fff" : "var(--text-secondary)" }}>
                {l}{last?.channels && <span className="ml-1 tnum opacity-80">{(last.channels[k] || 0).toLocaleString()}</span>}
              </button>
            ))}
          </div>
          <form className="flex flex-1 gap-2 min-w-[260px]" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
            <input className="input flex-1" placeholder="Từ khoá trong nội dung / advertiser / landing page…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="btn btn-primary" type="submit">Lọc</button>
          </form>
        </div>
        <div className="mt-3"><NetworkChips value={network} onChange={setNetwork} channel={channel} facets={last?.facets} networks={networks} /></div>
        <div className="mt-3"><MarketPicker value={country} onChange={setCountry} /></div>
        <div className="flex flex-wrap gap-2 mt-3 items-center">
          {channel === "ads" && (
            <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
              {FUNNELS.map(([k, l]) => (
                <button key={k} onClick={() => setFunnel(k)} className="text-xs px-3 py-1.5"
                        style={{ background: funnel === k ? "var(--series-1)" : "var(--surface-1)", color: funnel === k ? "#fff" : "var(--text-secondary)" }}>{l}</button>
              ))}
            </div>
          )}
          <select className="input" value={media} onChange={(e) => setMedia(e.target.value)}>
            <option value="">Mọi loại media</option><option value="video">Video</option><option value="image">Ảnh</option>
            <option value="carousel">Carousel</option><option value="text">Chữ (Search)</option>
          </select>
          {channel === "ads" && (
            <select className="input" value={active} onChange={(e) => setActive(e.target.value)}>
              <option value="true">Đang chạy</option><option value="false">Đã dừng</option><option value="">Tất cả</option>
            </select>
          )}
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            {SORTS[channel].map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <span className="text-sm text-ink2 ml-auto">{total.toLocaleString()} {channel === "commerce" ? "listing" : "quảng cáo"}</span>
        </div>
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? (
        <Card><Empty>Chưa có dữ liệu khớp bộ lọc. Vào <b>Tìm sản phẩm</b>, nhập từ khoá và bấm “Tìm trực tiếp trên nguồn” — app quét đồng thời mọi nguồn đang bật (Meta, TikTok, Snapchat, AliExpress).</Empty></Card>
      ) : (
        <>
          {channel === "commerce" ? (
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4 items-start">
              {rows.map((l: any) => <ListingCard key={l.id} l={l} byKey={byKey} />)}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 items-start">
              {rows.map((ad: any) => <AdCard key={ad.id} ad={ad} />)}
            </div>
          )}
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
