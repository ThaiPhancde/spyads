"use client";
import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { MarketPicker } from "@/components/MarketPicker";
import { ListingCard } from "@/components/ListingCard";
import { LoadMore } from "@/components/paging";
import { NetworkChips, usePlatforms } from "@/components/platforms";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";
import { api, fmt, qs, usePaged } from "@/lib/api";

const CACHE_HOURS = 72;  // same keyword + sources within this window → reuse the earlier search, no new crawl / Apify spend

export default function Marketplace() {
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const [country, setCountry] = useState("");
  const [network, setNetwork] = useState("");
  const [sort, setSort] = useState("sold");
  const [paid, setPaid] = useState(false);
  const [live, setLive] = useState<{ busy: boolean; msg: string; bad?: boolean } | null>(null);
  const [tick, setTick] = useState(0);
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  const { networks, byKey } = usePlatforms();
  const path = `/ads${qs({ q: applied, country, sort, channel: "commerce", network, t: tick })}`;

  /** "Tìm mới trên 1688": live job on the 1688 AK API (+ Apify fallback when ticked); "Lọc" never leaves the DB. */
  async function search1688() {
    const kw = q.trim();
    if (!kw) return setLive({ busy: false, msg: "Nhập từ khoá trước.", bad: true });
    setLive({ busy: true, msg: "Đang kiểm tra dữ liệu đã lưu…" });
    setApplied(kw); setNetwork("1688"); setCountry("");  // show the 1688 list right away: new listings appear while the job runs
    const t0 = Date.now();
    try {
      const { job_id, reused } = await api("/search/live", { method: "POST", body: JSON.stringify({
        query: kw, countries: ["ALL"], limit: 80, reuse_hours: CACHE_HOURS, adapters: paid ? ["ali1688", "apify_1688"] : ["ali1688"] }) });
      let j: any;
      const deadline = Date.now() + 5 * 60_000;  // a queued / slow Apify run can hold the job: stop waiting, it keeps running
      for (;;) {
        j = await api(`/search/jobs/${job_id}`);
        if (!alive.current) return;
        if (j.status !== "running") break;
        if (Date.now() > deadline) return setLive({ busy: false, msg: "Vẫn đang chạy nền — bấm Lọc lại sau ít phút để xem kết quả." });
        const got = Object.values(j.sources || {}).reduce((a: number, v: any) => a + (v.listings ?? v.stored ?? 0), 0);
        setLive({ busy: true, msg: `Đang tìm trên 1688 và lưu sản phẩm… ${Math.round((Date.now() - t0) / 1000)}s${got ? ` · đã lưu ${got} listing` : ""}` });
        if (got) setTick((t) => t + 1);
        await new Promise((r) => setTimeout(r, 2000));
      }
      const src: any = Object.values(j.sources || {})[0] || {};
      const n = src.listings ?? src.stored ?? 0;
      const err = src.error || j.error;
      if (!n) {
        setLive({ busy: false, bad: true, msg: `Không thể lấy thêm từ 1688: ${err || "không có kết quả"}` +
          (paid ? "" : " — có thể bật “Dùng Apify” (có thể phát sinh phí).") });
      } else {
        setLive({ busy: false, bad: !!err, msg: (reused ? `Dùng kết quả đã lưu: ${n} listing (tìm lúc ${fmt.dt(j.created_at)}, hạn ${CACHE_HOURS}h).`
          : `Xong: ${n} listing 1688 (${src.new ?? 0} mới).`) + (err ? ` Lưu ý: ${err}` : "") });
      }
      setTick((t) => t + 1);
    } catch (e: any) {
      setLive({ busy: false, bad: true, msg: String(e.message || e) });
    }
  }
  const { rows, total, last, loading, error, hasMore, loadMore } = usePaged(path, 24);

  return (
    <div>
      <PageHeader title="Nguồn hàng Trung Quốc" subtitle="AliExpress, 1688, Taobao: giá nhập và số đã bán. Mỗi listing được nối vào cùng sản phẩm với quảng cáo — giá nhập là mốc để tính biên lợi nhuận." />
      <Card className="mb-4">
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); setApplied(q.trim()); }}>
          <div className="relative min-w-[200px] flex-1">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input className="h-8 pl-8" placeholder="Tên sản phẩm / brand / seller…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <Button size="sm" type="submit">Lọc</Button>
          {network === "1688" && <Button variant="outline" size="sm" type="button" disabled={live?.busy} onClick={search1688}>{live?.busy ? <><Spinner /> Đang tìm…</> : "Tìm mới trên 1688"}</Button>}
        </form>
        {network === "1688" && (
          <div className="mt-2 flex items-center gap-1.5">
            <Checkbox id="mk-paid" checked={paid} onCheckedChange={(v) => setPaid(v === true)} />
            <Label htmlFor="mk-paid" className="text-xs font-normal text-ink2">Dùng Apify nếu 1688 trực tiếp thiếu dữ liệu (có thể phát sinh phí)</Label>
          </div>
        )}
        {live && network === "1688" && <div className={cn("mt-2 text-sm", live.bad && "text-critical")}>{live.msg}</div>}
        <div className="mt-3"><NetworkChips value={network} onChange={setNetwork} channel="commerce" facets={last?.facets} networks={networks} /></div>
        <div className="mt-3"><MarketPicker value={country} onChange={setCountry} /></div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <SimpleSelect value={sort} onChange={setSort} className="w-44"
                        options={[["sold", "Bán nhiều nhất"], ["reviews", "Nhiều đánh giá nhất"], ["price", "Giá thấp nhất"], ["recent", "Mới thu thập"]]} />
          <Badge variant="outline" className="tnum ml-auto">{total.toLocaleString()} listing</Badge>
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
