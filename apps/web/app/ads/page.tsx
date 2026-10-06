"use client";
import { MarketPicker } from "@/components/MarketPicker";
import { useEffect, useState } from "react";
import { AdCard } from "@/components/AdCard";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { qs, usePaged } from "@/lib/api";
import { useEvents, useTeam } from "@/lib/realtime";

const FUNNELS: [string, string][] = [["", "Tất cả"], ["mess", "💬 MKT Mess"], ["ladi", "🧾 MKT Ladi"], ["form", "Form"]];

export default function AdsLibrary() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [country, setCountry] = useState("");
  const [funnel, setFunnel] = useState("");
  const team = useTeam();
  useEffect(() => { setFunnel(team); }, [team]);
  const [media, setMedia] = useState("");
  const [active, setActive] = useState("true");
  const [sort, setSort] = useState("newest");
  const path = `/ads${qs({ q: applied, country, funnel, media_type: media, active, sort })}`;
  const { rows, total, loading, error, hasMore, loadMore, reload } = usePaged(path, 24);
  useEvents((e) => { if (e.type === "CREATIVE_STORED") reload(); });

  return (
    <div>
      <PageHeader title="Thư viện quảng cáo" subtitle="Từng quảng cáo thật đã thu thập, hiển thị như trong Meta Ad Library — kèm video/ảnh tải về được. Cuộn xuống để tải thêm." />
      <Card className="mb-4">
        <form className="flex flex-wrap gap-2 pt-3" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
          <input className="input flex-1 min-w-[220px]" placeholder="Từ khoá trong nội dung / tên page / landing page…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-primary" type="submit">Lọc</button>
        </form>
        <div className="mt-3"><MarketPicker value={country} onChange={setCountry} /></div>
        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
            {FUNNELS.map(([k, l]) => (
              <button key={k} onClick={() => setFunnel(k)} className="text-xs px-3 py-1.5"
                      style={{ background: funnel === k ? "var(--series-1)" : "var(--surface-1)", color: funnel === k ? "#fff" : "var(--text-secondary)" }}>{l}</button>
            ))}
          </div>
          <select className="input" value={media} onChange={(e) => setMedia(e.target.value)}>
            <option value="">Mọi loại media</option><option value="video">Video</option><option value="image">Ảnh</option><option value="carousel">Carousel</option>
          </select>
          <select className="input" value={active} onChange={(e) => setActive(e.target.value)}>
            <option value="true">Đang chạy</option><option value="false">Đã dừng</option><option value="">Tất cả</option>
          </select>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="newest">Mới chạy nhất</option><option value="longest">Chạy lâu nhất</option><option value="variants">Nhiều phiên bản nhất</option><option value="recent">Mới thu thập</option>
          </select>
          <span className="text-sm text-ink2 ml-auto">{total.toLocaleString()} quảng cáo</span>
        </div>
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? (
        <Card><Empty>Chưa có quảng cáo khớp bộ lọc. Vào <b>Tìm sản phẩm</b>, nhập từ khoá và bấm “Tìm trực tiếp trên nguồn” để lấy thêm từ Meta Ad Library.</Empty></Card>
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
