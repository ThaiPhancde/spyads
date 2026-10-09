"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { AlertCircle, AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, Info, Settings2, Zap } from "lucide-react";
import { ProductMediaCard } from "@/components/media";
import { liveCountries, MarketPicker } from "@/components/MarketPicker";
import { LoadMore } from "@/components/paging";
import { Card, Empty, Loading, PageHeader } from "@/components/ui";
import { splitTags as split, TagInput } from "@/components/filters/tag-input";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, qs, useApi, usePaged } from "@/lib/api";
import { NetworkBadge, usePlatforms } from "@/components/platforms";
import { getUser, useTeam } from "@/lib/realtime";
import { cn } from "@/lib/utils";

const FUNNELS: [string, string][] = [["", "Tất cả funnel"], ["mess", "💬 MKT Mess (inbox/WhatsApp)"], ["ladi", "🧾 MKT Ladi (landing page)"], ["form", "Form"]];
const SORTS: [string, string][] = [["opportunity", "Opportunity"], ["wave", "Wave potential"], ["novelty", "Độ mới lạ"],
  ["creative", "Creative potential"], ["demand", "Market demand"], ["growth", "Tăng trưởng 7d"], ["newest", "Mới phát hiện"], ["ads", "Số ads"]];

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
  const [limit, setLimit] = useState(50);  // ads / market crawled per results page
  const [liveMedia, setLiveMedia] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobIds, setJobIds] = useState("");  // sản phẩm của trang đang xem — hiện đúng những gì vừa lấy được
  const [page, setPageState] = useState(0);  // mỗi trang = 1 lượt crawl mới từ nguồn
  const pageRef = useRef(0);
  const pages = useRef<number[][]>([]);  // id sản phẩm của các trang đã crawl — quay lại trang cũ không crawl lại
  const setPage = (i: number) => { pageRef.current = i; setPageState(i); };
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
  // chip tắt = cũng lọc danh sách kết quả theo nền tảng (không chỉ bỏ nguồn khi quét)
  const nets = (l: any[]) => [...new Set(l.map((c) => c.network))];
  const picked = nets(sources.filter((c) => !off.includes(c.adapter)));
  const network = picked.length < nets(sources).length ? picked.join(",") : "";

  const exclude = exTags.join("|");  // chỉ tag đã chốt — tránh tải lại theo từng phím gõ
  const path = `/search${qs({ q: submitted, exclude, country: scope, funnel, sort, has_video: videoOnly, min_days: minDays, max_advertisers: maxAdv,
                             ids: jobIds, min_networks: crossOnly ? 2 : "", network })}`;
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
        if (j.product_ids?.length) { pages.current[pageRef.current] = j.product_ids; setJobIds(j.product_ids.join(",")); }
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
      pages.current = []; setPage(0); setJobIds("0");  // "0" = chưa có sản phẩm nào cho tới khi trang 1 về
      setJob({ id: r.job_id, status: "running", found: 0, new_ads: 0, has_more: false, error: null });
      poll(r.job_id);
    } catch (e: any) { setMsg(`Không bắt đầu được: ${e.message}`); }
  };
  const goto = (i: number) => { setPage(i); setJobIds((pages.current[i] || [0]).join(",")); };
  const next = async () => {
    if (!job) return;
    if (pages.current[page + 1]) return goto(page + 1);  // đã crawl rồi: lấy từ kho
    try {
      await api(`/search/jobs/${job.id}/more`, { method: "POST", body: JSON.stringify({ count: limit }) });
      setPage(page + 1); setJobIds("0");
      setJob({ ...job, status: "running", found: 0, new_ads: 0 });
      poll(job.id);
    } catch (e: any) { setMsg(e.message); }
  };
  const running = job?.status === "running";

  return (
    <div>
      <PageHeader title="Tìm sản phẩm" subtitle="Tìm trong kho, hoặc quét trực tiếp mọi nguồn đang bật cùng lúc — quảng cáo (Meta Ad Library, TikTok Ads) và nguồn hàng TQ (AliExpress, 1688, Taobao) để biết giá nhập. Kết quả gộp theo sản phẩm." />
      <Card className="mb-4">
        <div className="flex flex-wrap gap-2">
          <TagInput tags={tags} setTags={setTags} draft={q} setDraft={setQ} onSubmit={(l) => (l ? live() : submit())}
                    placeholder="Nhiều từ khoá, Enter sau mỗi từ: fengshui ⏎ lucky bracelet ⏎ pixiu ⏎ (khớp BẤT KỲ từ nào)" />
          <Button type="button" variant="outline" onClick={submit}>Tìm trong kho</Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <Button type="button" onClick={live} disabled={running || !kws().length}>
                  {running ? <><Spinner /> Đang tìm trên nguồn…</> : <><Zap /> Tìm trực tiếp trên nguồn</>}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>{!kws().length ? "Nhập từ khoá trước" : `Quét ngay ${active.length} nguồn đang bật × ${kws().length} từ khoá (Ctrl+Enter)`}</TooltipContent>
          </Tooltip>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="w-20 text-xs text-muted-foreground">Loại trừ:</span>
          <TagInput tags={exTags} setTags={setExTags} draft={exDraft} setDraft={setExDraft} tone="destructive"
                    placeholder="kids, children, DIY, free pattern … (bỏ sản phẩm chứa từ này)" />
        </div>
        <div className="mt-3"><MarketPicker value={scope} onChange={(v) => { setJobIds(""); setScope(v); }} /></div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={funnel} onValueChange={(v) => v !== undefined && setFunnel(v)} className="flex-wrap">
            {FUNNELS.map(([k, l]) => <ToggleGroupItem key={k} value={k} className="text-xs" aria-label={l}>{l}</ToggleGroupItem>)}
          </ToggleGroup>
          <Select value={sort} onValueChange={setSort}>
            <SelectTrigger size="sm" className="text-xs"><SelectValue /></SelectTrigger>
            <SelectContent>{SORTS.map(([k, l]) => <SelectItem key={k} value={k}>Sắp xếp: {l}</SelectItem>)}</SelectContent>
          </Select>
          <Input className="h-8 w-36 text-xs" placeholder="Chạy ≥ N ngày" inputMode="numeric" value={minDays} onChange={(e) => setMinDays(e.target.value.replace(/\D/g, ""))} />
          <Input className="h-8 w-40 text-xs" placeholder="≤ N advertiser" inputMode="numeric" value={maxAdv} onChange={(e) => setMaxAdv(e.target.value.replace(/\D/g, ""))} />
          <Label className="text-xs"><Checkbox checked={videoOnly} onCheckedChange={(v) => setVideoOnly(v === true)} /> Chỉ có video</Label>
          <Label className="text-xs" title="Sản phẩm xuất hiện trên ≥ 2 nền tảng (ads + nguồn hàng TQ) — đã được thị trường xác nhận chéo">
            <Checkbox checked={crossOnly} onCheckedChange={(v) => setCrossOnly(v === true)} /> Có trên ≥ 2 nền tảng</Label>
          <Collapsible className="contents">
            <CollapsibleTrigger asChild>
              <Button type="button" variant="outline" size="sm" className="text-xs group/adv"><Settings2 /> Tuỳ chọn quét <ChevronDown className="transition-transform group-data-[state=open]/adv:rotate-180" /></Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="basis-full">
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border bg-muted/40 px-3 py-2 text-xs">
                <span className="font-medium">Khi tìm trực tiếp:</span>
                <div className="flex items-center gap-2">
                  <span>Mỗi trang lấy</span>
                  <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))}>
                    <SelectTrigger size="sm" className="w-20 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent>{[20, 30, 50, 100].map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}</SelectContent>
                  </Select>
                  <span>quảng cáo / thị trường</span>
                </div>
                <div className="flex items-center gap-2">
                  <span>Loại</span>
                  <Select value={liveMedia || "all"} onValueChange={(v) => setLiveMedia(v === "all" ? "" : v)}>
                    <SelectTrigger size="sm" className="w-28 text-xs"><SelectValue /></SelectTrigger>
                    <SelectContent><SelectItem value="all">Tất cả</SelectItem><SelectItem value="video">Video</SelectItem><SelectItem value="image">Ảnh</SelectItem></SelectContent>
                  </Select>
                </div>
                <Label className="text-xs"><Checkbox checked={track} onCheckedChange={(v) => setTrack(v === true)} /> Theo dõi từ khoá này (tự quét lại mỗi giờ)</Label>
                <Label className="text-xs" title="Mỗi lượt quét Apify tốn tiền — chỉ bật khi cần"><Checkbox checked={paid} onCheckedChange={(v) => setPaid(v === true)} /> Dùng nguồn trả phí (Apify)</Label>
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
        {sources.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="mr-1 text-muted-foreground">Nguồn sẽ quét:</span>
            {sources.map((c) => {
              const on = active.includes(c);
              return (
                <Badge key={c.id} asChild variant="outline" className={cn("cursor-pointer px-1 py-0.5", on ? "border-series-1" : "opacity-45")}>
                  <button type="button" title={!paid && isPaid(c) ? `${c.name} — bật "Dùng nguồn trả phí" để quét` : c.name} disabled={!paid && isPaid(c)} aria-pressed={on}
                          onClick={() => setOff(on ? [...off, c.adapter] : off.filter((a) => a !== c.adapter))}>
                    <NetworkBadge network={c.network} byKey={byKey} small />
                  </button>
                </Badge>
              );
            })}
          </div>
        )}
        {job && (
          <Alert className="mt-3 bg-muted/40">
            {running ? <Spinner className="text-muted-foreground" /> : <Info />}
            <AlertDescription className="flex flex-wrap items-center gap-3 text-sm text-ink2">
            <span>
              {running ? `Đang quét trang ${page + 1}… ` : `Trang ${page + 1} đã quét xong: `}
              <b>{job.found}</b> ads / listing khác nhau đã lưu (<b>{job.new_ads}</b> lần đầu vào kho) · <b>{job.product_ids?.length ?? 0}</b> sản phẩm
              <Tooltip>
                <TooltipTrigger asChild><button type="button" className="ml-1 align-middle text-muted-foreground"><Info className="size-3.5" /></button></TooltipTrigger>
                <TooltipContent className="max-w-xs">Gồm mọi nguồn & mọi nước đã quét (cả listing AliExpress / 1688 / Taobao). Thư viện quảng cáo lọc theo thị trường + đang chạy nên con số ở đó nhỏ hơn.</TooltipContent>
              </Tooltip>
              {!running && !job.has_more && job.found > 0 ? " — nguồn đã hết kết quả" : ""}
            </span>
            <span className="flex items-center gap-1">
              <Button variant="outline" size="xs" disabled={running || page === 0} onClick={() => goto(page - 1)}><ChevronLeft /> Trang trước</Button>
              <Button variant="outline" size="xs" disabled={running || (!job.has_more && !pages.current[page + 1])} onClick={next}
                      title="Trang sau = crawl mới tiếp từ chỗ dừng (chỉ metadata + ảnh bìa nhỏ). Nguồn AliExpress / 1688 / Taobao / Apify chỉ quét ở trang 1.">
                Trang sau <ChevronRight />
              </Button>
            </span>
            </AlertDescription>
          </Alert>
        )}
        {job?.sources && Object.keys(job.sources).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {Object.entries(job.sources).map(([name, s]) => (
              <Tooltip key={name}>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className={cn("gap-1 px-2 py-0.5", s.error && "border-critical")}>
                    <NetworkBadge network={s.network} byKey={byKey} small />
                    {s.error ? <span className="text-critical">lỗi</span>
                      : <span>{s.stored ?? s.fetched}{s.new ? ` · ${s.new} mới` : ""}{s.failed ? ` · ${s.failed} không lưu được` : ""}</span>}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent className="max-w-xs">{s.error || `${name} — ${s.fetched} dòng nhận về (gồm trùng)`}</TooltipContent>
              </Tooltip>
            ))}
          </div>
        )}
        {job && !running && jobIds && (
          <div className="mt-1 text-xs text-muted-foreground">
            Danh sách dưới = sản phẩm của lượt quét này, chỉ tính ads <b>đang chạy</b>{funnel ? <> thuộc funnel <b>{funnel}</b></> : null}
            {network ? `, từ ${network}` : ""}{videoOnly ? ", có video" : ""}{crossOnly ? ", có trên ≥ 2 nền tảng" : ""} — bỏ bớt bộ lọc nếu thấy ít hơn số sản phẩm ở trên.
            <button type="button" className="ml-2 underline" onClick={() => setJobIds("")}>Xem toàn bộ kho khớp từ khoá</button>
          </div>
        )}
        {msg && <Alert variant="destructive" className="mt-2"><AlertCircle /><AlertTitle>Lỗi</AlertTitle><AlertDescription>{msg}</AlertDescription></Alert>}
        {!running && job?.error && job.found > 0 && (
          <div className="mt-2 flex items-start gap-1 text-xs text-ink2"><AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" /> Một số nguồn lỗi (các nguồn khác vẫn trả kết quả): {job.error}</div>
        )}
      </Card>

      {error ? <Loading error={error} /> : rows.length === 0 && loading ? <Loading /> : (
        <>
          <div className="mb-3 text-sm text-ink2">
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
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
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
