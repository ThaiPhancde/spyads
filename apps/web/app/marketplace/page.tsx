"use client";
import { useState } from "react";
import { MarketPicker } from "@/components/MarketPicker";
import { ListingCard } from "@/components/ListingCard";
import { LoadMore } from "@/components/paging";
import { NetworkChips, usePlatforms } from "@/components/platforms";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { qs, usePaged } from "@/lib/api";

export default function Marketplace() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [country, setCountry] = useState("");
  const [network, setNetwork] = useState("");
  const [sort, setSort] = useState("sold");
  const { networks, byKey } = usePlatforms();
  const path = `/ads${qs({ q: applied, country, sort, channel: "commerce", network })}`;
  const { rows, total, last, loading, error, hasMore, loadMore } = usePaged(path, 24);

  return (
    <div>
      <PageHeader title="Nguồn hàng Trung Quốc" subtitle="AliExpress, 1688, Taobao: giá nhập và số đã bán. Mỗi listing được nối vào cùng sản phẩm với quảng cáo — giá nhập là mốc để tính biên lợi nhuận." />
      <Card className="mb-4">
        <form className="flex flex-wrap gap-2 pt-3" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
          <input className="input flex-1 min-w-[220px]" placeholder="Tên sản phẩm / brand / seller…" value={q} onChange={(e) => setQ(e.target.value)} />
          <button className="btn btn-primary" type="submit">Lọc</button>
        </form>
        <div className="mt-3"><NetworkChips value={network} onChange={setNetwork} channel="commerce" facets={last?.facets} networks={networks} /></div>
        <div className="mt-3"><MarketPicker value={country} onChange={setCountry} /></div>
        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="sold">Bán nhiều nhất</option><option value="reviews">Nhiều đánh giá nhất</option><option value="price">Giá thấp nhất</option><option value="recent">Mới thu thập</option>
          </select>
          <span className="text-sm text-ink2 ml-auto">{total.toLocaleString()} listing</span>
        </div>
      </Card>
      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : rows.length === 0 ? (
        <Card><Empty>Chưa có listing khớp bộ lọc. AliExpress (và 1688 / Taobao nếu đã gắn Apify) chạy khi bạn <b>Tìm sản phẩm</b> bằng từ khoá.</Empty></Card>
      ) : (
        <>
          <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5 gap-4 items-start">
            {rows.map((l: any) => <ListingCard key={l.id} l={l} byKey={byKey} />)}
          </div>
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}
