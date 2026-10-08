"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { ProductMediaCard } from "@/components/media";
import { liveCountries, MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { api, qs, useApi, usePaged } from "@/lib/api";
import { NetworkBadge, usePlatforms } from "@/components/platforms";
import { getUser, useTeam } from "@/lib/realtime";

const FUNNELS: [string, string][] = [["", "Tất cả funnel"], ["mess", "💬 MKT Mess (inbox/WhatsApp)"], ["ladi", "🧾 MKT Ladi (landing page)"], ["form", "Form"]];
const SORTS: [string, string][] = [["opportunity", "Opportunity"], ["wave", "Wave potential"], ["novelty", "Độ mới lạ"],
  ["creative", "Creative potential"], ["demand", "Market demand"], ["growth", "Tăng trưởng 7d"], ["newest", "Mới phát hiện"], ["ads", "Số ads"]];

const split = (s: string) => s.split(/[|,\n]/).map((x) => x.trim()).filter(Boolean);

/** Gõ từ khoá → Enter (hoặc dấu phẩy) thành tag; × để xoá; Backspace ở ô trống xoá tag cuối. */
function TagInput({ tags, setTags, draft, setDraft, placeholder, tone, onSubmit }: {
  tags: string[]; setTags: (t: string[]) => void; draft: string; setDraft: (s: string) => void; placeholder: string; tone?: string;
  onSubmit?: (live: boolean) => void }) {
  const add = (s: string) => { const n = split(s).filter((x) => !tags.some((t) => t.toLowerCase() === x.toLowerCase())); if (n.length) setTags([...tags, ...n]); setDraft(""); };
  return (
    <div className="input flex flex-wrap items-center gap-1 flex-1 min-w-[240px] text-sm">
      {tags.map((t) => (
        <span key={t} className="rounded-full border px-2 py-0.5 text-xs flex items-center gap-1" style={{ borderColor: tone || "var(--series-1)" }}>
          {t}<button type="button" aria-label={`Xoá ${t}`} onClick={() => setTags(tags.filter((x) => x !== t))}>×</button>
        </span>
      ))}
      <input className="flex-1 min-w-[140px] bg-transparent outline-none" value={draft} placeholder={tags.length ? "+ Thêm từ khoá" : placeholder}
             onChange={(e) => e.target.value.includes(",") ? add(e.target.value) : setDraft(e.target.value)}
             onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/[|,\n]/.test(t)) { e.preventDefault(); add(draft + t); } }}
             onKeyDown={(e) => {
               if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSubmit?.(true); }
               else if (e.key === "Enter" && draft.trim()) { e.preventDefault(); add(draft); }
               else if (e.key === "Enter" && tags.length) { e.preventDefault(); onSubmit?.(false); }
               else if (e.key === "Backspace" && !draft && tags.length) setTags(tags.slice(0, -1));
             }} />
    </div>
  );
}

type Src = { network: string; fetched: number; stored?: number; new: number; failed: number; error?: string | null };
type Job = { id: number; status: string; found: number; new_ads: number; has_more: boolean; error: string | null; products?: number;
  product_ids?: number[]; sources?: Record<string, Src> };

function SearchPage() {
  const sp = useSearchParams();
  const [tags, setTags] = useState<string[]>(split(sp.get("q") || ""));
  const [q, setQ] = useState("");  // ô đang gõ, chưa thành tag
  const [exTags, setExTags] = useState<string[]>(split(sp.get("exclude") || ""));
  const [exDraft, setExDraft] = useState("");
  const [submitted, setSubmitted] = useState(sp.get("q") || "");
  const kws = () => [...tags, ...split(q)];  // tính cả chữ đang gõ dở
  const [scope, setScope] = useState(sp.get("market") || "");  // "" = mọi thị trường mục tiêu (không gồm VN)
  const [funnel, setFunnel] = useState(sp.get("funnel") || "");
  const team = useTeam();
  useEffect(() => { if (team && !sp.get("funnel")) setFunnel(team); }, [team]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [sort, setSort] = useState("opportunity");
  const [videoOnly, setVideoOnly] = useState(false);
  const [minDays, setMinDays] = useState("");
  const [maxAdv, setMaxAdv] = useState("");
  const [track, setTrack] = useState(false);
  const [limit, setLimit] = useState(100);
  const [liveMedia, setLiveMedia] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobIds, setJobIds] = useState("");  // sản phẩm của lượt quét trực tiếp — hiện đúng những gì vừa lấy được
  const [msg, setMsg] = useState("");
  const [crossOnly, setCrossOnly] = useState(false);
  const [off, setOff] = useState<string[]>([]);  // live-search sources the user switched off for this search
  const [paid, setPaidState] = useState(false);  // Apify (paid) connectors are opt-in, remembered per browser
  useEffect(() => { try { setPaidState(localStorage.getItem("mi_paid") === "1"); } catch {} }, []);
  const setPaid = (v: boolean) => { setPaidState(v); try { localStorage.setItem("mi_paid", v ? "1" : "0"); } catch {} };
  const timer = useRef<any>(null);
  const after = useRef<any>(null);  // post-done refresh loop
  const { byKey } = usePlatforms();
  const coll = useApi("/collector");
  const sources: any[] = (coll.data?.connectors || []).filter((c: any) => c.enabled && c.supports_search);
  const isPaid = (c: any) => String(c.adapter).startsWith("apify");
  const active = sources.filter((c) => !off.includes(c.adapter) && (paid || !isPaid(c)));

  const exclude = exTags.join("|");  // chỉ tag đã chốt — tránh tải lại theo từng phím gõ
  const path = `/search${qs({ q: submitted, exclude, country: scope, funnel, sort, has_video: videoOnly, min_days: minDays, max_advertisers: maxAdv,
                             ids: jobIds, min_networks: crossOnly ? 2 : "" })}`;
  const { rows, total, loading, error, hasMore, loadMore, reload, last } = usePaged(path, 36);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;  // poller always calls the reload bound to the current path (ids/filters)

  const poll = (id: number) => {
    clearInterval(timer.current); clearInterval(after.current);
    let n = 0;
    timer.current = setInterval(async () => {
      try {
        const j = await api(`/search/jobs/${id}`);
        setJob(j);
        if (j.product_ids?.length) setJobIds(j.product_ids.join(","));
        if (++n % 2 === 0 || j.status !== "running") reloadRef.current();
        if (j.status !== "running") {
          clearInterval(timer.current);
          setMsg(j.error && !j.found ? `Lỗi: ${j.error}` : "");
          // video đang tải về kho — làm mới vài lần để thấy ảnh bìa
          let k = 0; after.current = setInterval(() => { reloadRef.current(); if (++k >= 8) clearInterval(after.current); }, 8000);
        }
      } catch (e: any) { clearInterval(timer.current); setMsg(`Mất kết nối khi theo dõi: ${e.message}`); setJob((x) => x && { ...x, status: "error" }); }
    }, 2500);
  };
  useEffect(() => () => { clearInterval(timer.current); clearInterval(after.current); }, []);

  const submit = () => { const k = kws(); if (exDraft.trim()) { setExTags([...exTags, ...split(exDraft)]); setExDraft(""); } if (q.trim()) { setTags(k); setQ(""); } setJobIds(""); setSubmitted(k.join("|")); return k; };
  const live = async () => {
    const k = submit();
    if (!k.length) return;
    setMsg("");
    try {
      const r = await api("/search/live", { method: "POST", body: JSON.stringify({
        query: k.join("|"), countries: liveCountries(scope), user: getUser().name, track, limit, media_type: liveMedia || null, paid,
        adapters: active.length < sources.length ? active.map((c) => c.adapter) : [] }) });
      setJob({ id: r.job_id, status: "running", found: 0, new_ads: 0, has_more: false, error: null });
      poll(r.job_id);
    } catch (e: any) { setMsg(`Không bắt đầu được: ${e.message}`); }
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
      <PageHeader title="Tìm sản phẩm" subtitle="Tìm trong kho, hoặc quét trực tiếp mọi nguồn đang bật cùng lúc — quảng cáo (Meta Ad Library, TikTok Ads) và nguồn hàng TQ (AliExpress, 1688, Taobao) để biết giá nhập. Kết quả gộp theo sản phẩm." />
      <Card className="mb-4">
        <div className="flex flex-wrap gap-2 pt-3">
          <TagInput tags={tags} setTags={setTags} draft={q} setDraft={setQ} onSubmit={(l) => (l ? live() : submit())}
                    placeholder="Nhiều từ khoá, Enter sau mỗi từ: fengshui ⏎ lucky bracelet ⏎ pixiu ⏎ (khớp BẤT KỲ từ nào)" />
          <button type="button" className="btn" onClick={submit}>Tìm trong kho</button>
          <button type="button" className="btn btn-primary" onClick={live} disabled={running || !kws().length}
                  title={!kws().length ? "Nhập từ khoá trước" : `Quét ngay ${active.length} nguồn đang bật × ${kws().length} từ khoá (Ctrl+Enter)`}>
            {running ? "Đang tìm trên nguồn…" : "⚡ Tìm trực tiếp trên nguồn"}
          </button>
        </div>
        <div className="flex flex-wrap gap-2 mt-2 items-center">
          <span className="text-xs text-muted w-20">Loại trừ:</span>
          <TagInput tags={exTags} setTags={setExTags} draft={exDraft} setDraft={setExDraft} tone="var(--critical)"
                    placeholder="kids, children, DIY, free pattern … (bỏ sản phẩm chứa từ này)" />
        </div>
        <div className="mt-3"><MarketPicker value={scope} onChange={(v) => { setJobIds(""); setScope(v); }} /></div>
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
          <label className="text-xs flex items-center gap-1" title="Sản phẩm xuất hiện trên ≥ 2 nền tảng (ads + nguồn hàng TQ) — đã được thị trường xác nhận chéo">
            <input type="checkbox" checked={crossOnly} onChange={(e) => setCrossOnly(e.target.checked)} /> Có trên ≥ 2 nền tảng</label>
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
          <label className="flex items-center gap-1" title="Mỗi lượt quét Apify tốn tiền — chỉ bật khi cần"><input type="checkbox" checked={paid} onChange={(e) => setPaid(e.target.checked)} /> Dùng nguồn trả phí (Apify)</label>
        </div>
        {sources.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2 items-center text-xs">
            <span className="text-muted mr-1">Nguồn sẽ quét:</span>
            {sources.map((c) => {
              const on = active.includes(c);
              return (
                <button key={c.id} type="button" title={!paid && isPaid(c) ? `${c.name} — bật "Dùng nguồn trả phí" để quét` : c.name} disabled={!paid && isPaid(c)}
                        onClick={() => setOff(on ? [...off, c.adapter] : off.filter((a) => a !== c.adapter))}
                        className="rounded-full border px-1 py-0.5" style={{ borderColor: on ? "var(--series-1)" : "var(--border)", opacity: on ? 1 : 0.45 }}>
                  <NetworkBadge network={c.network} byKey={byKey} small />
                </button>
              );
            })}
          </div>
        )}
        {job && (
          <div className="text-sm mt-3 flex flex-wrap items-center gap-3">
            <span className="text-ink2">
              {running ? "Đang quét… " : "Đã quét xong: "}
              <b>{job.found}</b> ads / listing khác nhau đã lưu (<b>{job.new_ads}</b> lần đầu vào kho) · <b>{job.product_ids?.length ?? 0}</b> sản phẩm
              <span className="text-muted" title="Gồm mọi nguồn & mọi nước đã quét (cả listing AliExpress / 1688 / Taobao). Thư viện quảng cáo lọc theo thị trường + đang chạy nên con số ở đó nhỏ hơn."> ⓘ</span>
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
        {job?.sources && Object.keys(job.sources).length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2 text-xs">
            {Object.entries(job.sources).map(([name, s]) => (
              <span key={name} title={s.error || name} className="rounded-full border px-2 py-0.5 flex items-center gap-1"
                    style={{ borderColor: s.error ? "var(--critical)" : "var(--border)" }}>
                <NetworkBadge network={s.network} byKey={byKey} small />
                {s.error ? <span style={{ color: "var(--critical)" }}>lỗi</span>
                  : <span title={`${s.fetched} dòng nhận về (gồm trùng)`}>{s.stored ?? s.fetched}{s.new ? ` · ${s.new} mới` : ""}{s.failed ? ` · ${s.failed} không lưu được` : ""}</span>}
              </span>
            ))}
          </div>
        )}
        {job && !running && jobIds && (
          <div className="text-xs mt-1 text-muted">
            Danh sách dưới = sản phẩm của lượt quét này, chỉ tính ads <b>đang chạy</b>{funnel ? <> thuộc funnel <b>{funnel}</b></> : null}
            {videoOnly ? ", có video" : ""}{crossOnly ? ", có trên ≥ 2 nền tảng" : ""} — bỏ bớt bộ lọc nếu thấy ít hơn số sản phẩm ở trên.
            <button className="underline ml-2" onClick={() => setJobIds("")}>Xem toàn bộ kho khớp từ khoá</button>
          </div>
        )}
        {msg && <div className="text-sm mt-2" style={{ color: "var(--critical)" }}>{msg}</div>}
        {!running && job?.error && job.found > 0 && (
          <div className="text-xs mt-2 text-ink2">⚠ Một số nguồn lỗi (các nguồn khác vẫn trả kết quả): {job.error}</div>
        )}
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : (
        <>
          <div className="text-sm text-ink2 mb-3">
            {total.toLocaleString()} sản phẩm{submitted ? ` khớp “${split(submitted).join("” hoặc “")}”` : ""}{exclude ? ` · loại trừ: ${split(exclude).join(", ")}` : ""}
            {last?.funnels && Object.keys(last.funnels).length > 0 && <> · ads theo funnel: {Object.entries(last.funnels).map(([k, v]) => `${k} ${v}`).join(" · ")}</>}
          </div>
          {rows.length === 0 ? (
            <Card><Empty>
              Kho chưa có sản phẩm khớp{scope ? ` ở ${scope}` : ""}.<br />
              {kws().length ? <>Bấm <b>⚡ Tìm trực tiếp trên nguồn</b> để quét mọi nguồn đang bật ngay.</> : <>Nhập từ khoá vào ô trên, rồi bấm <b>⚡ Tìm trực tiếp trên nguồn</b>.</>}
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
