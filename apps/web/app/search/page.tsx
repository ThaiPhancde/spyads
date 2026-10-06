"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ProductMediaCard } from "@/components/media";
import { liveCountries, MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { api, qs, usePaged } from "@/lib/api";
import { getUser, useTeam } from "@/lib/realtime";

const FUNNELS: [string, string][] = [["", "Tất cả funnel"], ["mess", "💬 MKT Mess (inbox/WhatsApp)"], ["ladi", "🧾 MKT Ladi (landing page)"], ["form", "Form"]];
const SORTS: [string, string][] = [["opportunity", "Opportunity"], ["wave", "Wave potential"], ["novelty", "Độ mới lạ"],
  ["creative", "Creative potential"], ["demand", "Market demand"], ["growth", "Tăng trưởng 7d"], ["newest", "Mới phát hiện"], ["ads", "Số ads"]];

type Job = { id: number; status: string; found: number; new_ads: number; has_more: boolean; error: string | null; products?: number };

function SearchPage() {
  const sp = useSearchParams();
  const [q, setQ] = useState(sp.get("q") || "");
  const [submitted, setSubmitted] = useState(sp.get("q") || "");
  const [scope, setScope] = useState(sp.get("market") || "");  // "" = mọi thị trường mục tiêu (không gồm VN)
  const [funnel, setFunnel] = useState(sp.get("funnel") || "");
  const team = useTeam();
  useEffect(() => { setFunnel(team); }, [team]);
  const [sort, setSort] = useState("opportunity");
  const [videoOnly, setVideoOnly] = useState(false);
  const [minDays, setMinDays] = useState("");
  const [maxAdv, setMaxAdv] = useState("");
  const [track, setTrack] = useState(false);
  const [limit, setLimit] = useState(100);
  const [liveMedia, setLiveMedia] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [msg, setMsg] = useState("");
  const timer = useRef<any>(null);

  const path = `/search${qs({ q: submitted, country: scope, funnel, sort, has_video: videoOnly, min_days: minDays, max_advertisers: maxAdv })}`;
  const { rows, total, loading, error, hasMore, loadMore, reload, last } = usePaged(path, 36);

  const poll = (id: number) => {
    clearInterval(timer.current);
    let n = 0;
    timer.current = setInterval(async () => {
      try {
        const j = await api(`/search/jobs/${id}`);
        setJob(j);
        if (++n % 2 === 0 || j.status !== "running") reload();
        if (j.status !== "running") {
          clearInterval(timer.current);
          setMsg(j.error && !j.found ? `Lỗi: ${j.error}` : "");
          // video đang tải về kho — làm mới vài lần để thấy ảnh bìa
          let k = 0; const t = setInterval(() => { reload(); if (++k >= 8) clearInterval(t); }, 8000);
        }
      } catch {}
    }, 2500);
  };
  useEffect(() => () => clearInterval(timer.current), []);

  const live = async () => {
    if (!q.trim()) return;
    setSubmitted(q.trim());
    setMsg("");
    const r = await api("/search/live", { method: "POST", body: JSON.stringify({
      query: q.trim(), countries: liveCountries(scope), user: getUser().name, track, limit, media_type: liveMedia || null }) });
    setJob({ id: r.job_id, status: "running", found: 0, new_ads: 0, has_more: false, error: null });
    poll(r.job_id);
  };
  const more = async (count: number) => {
    if (!job) return;
    try {
      await api(`/search/jobs/${job.id}/more`, { method: "POST", body: JSON.stringify({ count }) });
      setJob({ ...job, status: "running" });
      poll(job.id);
    } catch (e: any) { setMsg(e.message); }
  };
  const running = job?.status === "running";

  return (
    <div>
      <PageHeader title="Tìm sản phẩm" subtitle="Tìm trong kho dữ liệu, hoặc tìm trực tiếp trên Meta Ad Library / các nguồn đã kết nối — kèm video tải về được" />
      <Card className="mb-4">
        <form onSubmit={(e) => { e.preventDefault(); setSubmitted(q.trim()); }} className="flex flex-wrap gap-2 pt-3">
          <input className="input flex-1 min-w-[240px] text-sm" value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="Từ khoá sản phẩm: neck massager, serum, máy hút bụi, ساعة …" />
          <button type="submit" className="btn">Tìm trong kho</button>
          <button type="button" className="btn btn-primary" onClick={live} disabled={running || !q.trim()}
                  title={!q.trim() ? "Nhập từ khoá trước" : "Quét Meta Ad Library ngay"}>
            {running ? "Đang tìm trên nguồn…" : "⚡ Tìm trực tiếp trên nguồn"}
          </button>
        </form>
        <div className="mt-3"><MarketPicker value={scope} onChange={setScope} /></div>
        <div className="flex flex-wrap gap-2 mt-3 items-center">
          <div className="flex rounded-lg border overflow-hidden" style={{ borderColor: "var(--border)" }}>
            {FUNNELS.map(([k, l]) => (
              <button key={k} onClick={() => setFunnel(k)} className="text-xs px-3 py-1.5"
                      style={{ background: funnel === k ? "var(--series-1)" : "var(--surface-1)", color: funnel === k ? "#fff" : "var(--text-secondary)" }}>{l}</button>
            ))}
          </div>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)}>{SORTS.map(([k, l]) => <option key={k} value={k}>Sắp xếp: {l}</option>)}</select>
          <input className="input w-36" placeholder="Chạy ≥ N ngày" value={minDays} onChange={(e) => setMinDays(e.target.value.replace(/\D/g, ""))} />
          <input className="input w-40" placeholder="≤ N advertiser" value={maxAdv} onChange={(e) => setMaxAdv(e.target.value.replace(/\D/g, ""))} />
          <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={videoOnly} onChange={(e) => setVideoOnly(e.target.checked)} /> Chỉ có video</label>
        </div>
        <div className="flex flex-wrap gap-3 mt-3 pt-3 items-center border-t text-xs" style={{ borderColor: "var(--grid)" }}>
          <span className="text-muted">Khi tìm trực tiếp:</span>
          <label className="flex items-center gap-1">Lấy
            <select className="input" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>{[50, 100, 200, 500, 1000].map((n) => <option key={n} value={n}>{n}</option>)}</select>
            quảng cáo / thị trường
          </label>
          <label className="flex items-center gap-1">Loại
            <select className="input" value={liveMedia} onChange={(e) => setLiveMedia(e.target.value)}><option value="">Tất cả</option><option value="video">Video</option><option value="image">Ảnh</option></select>
          </label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={track} onChange={(e) => setTrack(e.target.checked)} /> Theo dõi từ khoá này (tự quét lại mỗi giờ)</label>
        </div>
        {job && (
          <div className="text-sm mt-3 flex flex-wrap items-center gap-3">
            <span className="text-ink2">
              {running ? `Đang lấy… ${job.found} quảng cáo (${job.new_ads} mới)` : `Đã lấy ${job.found} quảng cáo từ nguồn (${job.new_ads} mới)`}
              {!running && !job.has_more && job.found > 0 ? " — nguồn đã hết kết quả" : ""}
            </span>
            {!running && job.has_more && (
              <>
                <button className="btn btn-primary text-xs" onClick={() => more(100)}>Tải thêm 100 từ nguồn</button>
                <button className="btn text-xs" onClick={() => more(300)}>+300</button>
              </>
            )}
          </div>
        )}
        {msg && <div className="text-sm mt-2" style={{ color: "var(--critical)" }}>{msg}</div>}
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : (
        <>
          <div className="text-sm text-ink2 mb-3">
            {total.toLocaleString()} sản phẩm{submitted ? ` khớp “${submitted}”` : ""}
            {last?.funnels && Object.keys(last.funnels).length > 0 && <> · ads theo funnel: {Object.entries(last.funnels).map(([k, v]) => `${k} ${v}`).join(" · ")}</>}
          </div>
          {rows.length === 0 ? (
            <Card><Empty>
              Kho chưa có sản phẩm khớp{scope ? ` ở ${scope}` : ""}.<br />
              {q.trim() ? <>Bấm <b>⚡ Tìm trực tiếp trên nguồn</b> để quét Meta Ad Library ngay.</> : <>Nhập từ khoá vào ô trên, rồi bấm <b>⚡ Tìm trực tiếp trên nguồn</b>.</>}
            </Empty></Card>
          ) : (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
                {rows.map((p: any) => <ProductMediaCard key={p.id} p={p} />)}
              </div>
              <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
            </>
          )}
        </>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><SearchPage /></Suspense>;
}
