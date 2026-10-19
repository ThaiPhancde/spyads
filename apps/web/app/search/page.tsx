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
import { GlobalImageSearch } from "@/components/visual/global-image-search";
import { AdvertiserFilters } from "@/components/workspace/actions";
import { useSession } from "@/components/workspace/session";
import { toast } from "sonner";

const FUNNELS: [string, string][] = [["", "Tất cả funnel"], ["mess", "💬 MKT Mess (inbox/WhatsApp)"], ["ladi", "🧾 MKT Ladi (landing page)"], ["form", "Form"]];
const SORTS: [string, string][] = [["fit", "Độ phù hợp với tôi"], ["opportunity", "Opportunity"], ["wave", "Wave potential"], ["novelty", "Độ mới lạ"],
  ["creative", "Creative potential"], ["demand", "Market demand"], ["growth", "Tăng trưởng 7d"], ["newest", "Mới phát hiện"], ["ads", "Số ads"]];

// Phase 7: personalized modes — every one is an exclusive-opportunity view (products another MKT owns never show)
const MODES: [string, string, string][] = [["", "Tất cả", "Toàn bộ kho khớp bộ lọc (sản phẩm đã có chủ vẫn hiện, có nhãn)"],
  ["for_me", "Dành cho tôi", "Xếp theo hồ sơ sở thích, lịch sử lưu / test và advertiser bạn Follow"],
  ["claimable", "Có thể nhận", "Chỉ sản phẩm chưa MKT nào nhận"],
  ["followed", "Đối thủ đang Follow", "Sản phẩm của advertiser bạn đang Follow"],
  ["similar", "Sản phẩm tương tự", "Tương tự một sản phẩm đã chọn, hoặc tương tự những gì bạn đã lưu / test"]];
const LANGS: [string, string][] = [["", "Mọi ngôn ngữ"], ["en", "English"], ["tl", "Tagalog"], ["vi", "Tiếng Việt"], ["ar", "العربية"],
  ["es", "Español"], ["de", "Deutsch"], ["fr", "Français"], ["zh", "中文"]];

/** Value that only follows `v` after it stops changing for `ms` — typing in a filter box doesn't refetch per key. */
function useDebounced<T>(v: T, ms = 350) {
  const [d, setD] = useState(v);
  useEffect(() => { const t = setTimeout(() => setD(v), ms); return () => clearTimeout(t); }, [v, ms]);
  return d;
}

// supplier marketplaces: searched on their own page (/marketplace), never mixed into the ad search
const SUPPLIER_NETWORKS = new Set(["aliexpress", "1688", "taobao"]);

type Src = { network: string; fetched: number; stored?: number; new: number; failed: number; error?: string | null };
type Job = { id: number; status: string; found: number; new_ads: number; has_more: boolean; error: string | null; products?: number;
  product_ids?: number[]; sources?: Record<string, Src> };
type SourceGroup = { key: string; network: string; connectors: any[] };
type ProgressGroup = { key: string; network: string; entries: [string, Src][] };

function groupTikTokSources(sources: any[]): SourceGroup[] {
  const groups: SourceGroup[] = [];
  for (const source of sources) {
    if (source.network === "tiktok") {
      const existing = groups.find((group) => group.key === "tiktok");
      if (existing) existing.connectors.push(source);
      else groups.push({ key: "tiktok", network: "tiktok", connectors: [source] });
    } else {
      groups.push({ key: String(source.id), network: source.network, connectors: [source] });
    }
  }
  return groups;
}

function groupTikTokProgress(sources: Record<string, Src>): ProgressGroup[] {
  const groups: ProgressGroup[] = [];
  for (const [name, source] of Object.entries(sources)) {
    if (source.network === "tiktok") {
      const existing = groups.find((group) => group.key === "tiktok");
      if (existing) existing.entries.push([name, source]);
      else groups.push({ key: "tiktok", network: "tiktok", entries: [[name, source]] });
    } else {
      groups.push({ key: name, network: source.network, entries: [[name, source]] });
    }
  }
  return groups;
}

// form state survives a visit to a product page (SPA back navigation) and a page refresh (sessionStorage, per tab)
let saved: any = (() => { try { return typeof window === "undefined" ? {} : JSON.parse(sessionStorage.getItem("mi_search") || "{}"); } catch { return {}; } })();

function SearchPage() {
  const { user } = useSession();
  const sp = useSearchParams();
  const S = sp.get("saved_filter") ? {} : saved;
  const [tags, setTags] = useState<string[]>(S.tags ?? split(sp.get("q") || ""));
  const [q, setQ] = useState("");  // ô đang gõ, chưa thành tag
  const [exTags, setExTags] = useState<string[]>(S.exTags ?? split(sp.get("exclude") || ""));
  const [exDraft, setExDraft] = useState("");
  const [submitted, setSubmitted] = useState(S.submitted ?? (sp.get("q") || ""));
  const kws = () => [...tags, ...split(q)];  // tính cả chữ đang gõ dở
  const [scope, setScope] = useState(S.scope ?? (sp.get("country") || sp.get("market") || ""));
  const [funnel, setFunnel] = useState(S.funnel ?? (sp.get("funnel") || ""));
  const team = useTeam();
  useEffect(() => { if (team && !sp.has("saved_filter") && !sp.has("funnel")) setFunnel(team); }, [team]);  // eslint-disable-line react-hooks/exhaustive-deps
  const [sort, setSort] = useState(S.sort ?? sp.get("sort") ?? "opportunity");
  const [videoOnly, setVideoOnly] = useState(S.videoOnly ?? (sp.get("has_video") === "true"));
  const [minDays, setMinDays] = useState(S.minDays ?? sp.get("min_days") ?? "");
  const [maxAdv, setMaxAdv] = useState(S.maxAdv ?? sp.get("max_advertisers") ?? "");
  const [track, setTrack] = useState(false);
  const [limit, setLimit] = useState(50);  // ads / market crawled per results page
  const [liveMedia, setLiveMedia] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobIds, setJobIds] = useState<string>(S.jobIds ?? "");  // sản phẩm của trang đang xem — hiện đúng những gì vừa lấy được
  const [page, setPageState] = useState(0);  // mỗi trang = 1 lượt crawl mới từ nguồn
  const pageRef = useRef(0);
  const pages = useRef<number[][]>([]);  // id sản phẩm của các trang đã crawl — quay lại trang cũ không crawl lại
  const setPage = (i: number) => { pageRef.current = i; setPageState(i); };
  const [msg, setMsg] = useState("");
  const [crossOnly, setCrossOnly] = useState(S.crossOnly ?? (Number(sp.get("min_networks")) >= 2));
  const [nameStatus, setNameStatus] = useState(S.nameStatus ?? (sp.get("name_status") || "ALL"));  // UNKNOWN = hàng chờ MKT đặt tên
  const [expand, setExpand] = useState(S.expand ?? (sp.get("expand") !== "false"));  // khớp cả bản dịch / đồng nghĩa (vi · en · tl · ar · zh) — xếp sau khớp đúng
  const [expandLive, setExpandLive] = useState(true);  // quét thêm từ khoá tương đương (en + ngôn ngữ thị trường): nguồn khớp lỏng từng từ nên từ khoá nước ngoài hay trả sai
  const [showOff, setShowOff] = useState(false);  // hiện cả sản phẩm nguồn trả về nhưng nội dung không nhắc tới từ khoá
  const [off, setOff] = useState<string[]>([]);  // live-search sources the user switched off for this search
  const [mode, setMode] = useState<string>(S.mode ?? (sp.get("mode") || ""));
  const [similarTo, setSimilarTo] = useState<string>(S.similarTo ?? (sp.get("similar_to") || ""));
  const [category, setCategory] = useState<string>(S.category ?? (sp.get("category") || ""));
  const [language, setLanguage] = useState<string>(S.language ?? (sp.get("language") || ""));
  const [minFit, setMinFit] = useState<string>(S.minFit ?? (sp.get("min_fit") || ""));
  const [minDemand, setMinDemand] = useState<string>(S.minDemand ?? (sp.get("min_demand") || ""));
  const [minEng, setMinEng] = useState<string>(S.minEng ?? (sp.get("min_engagement") || ""));
  const meta = useApi("/meta");
  const pickMode = (m: string) => { setMode(m); setJobIds(""); if (m && sort !== "fit") setSort("fit"); if (!m && sort === "fit") setSort("opportunity"); if (m !== "similar") setSimilarTo(""); };
  const findSimilar = (id: number) => { setSimilarTo(String(id)); setMode("similar"); setSort("fit"); setJobIds(""); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const [paid, setPaidState] = useState(false);  // Apify (paid) connectors are opt-in, remembered per browser
  useEffect(() => { try { setPaidState(localStorage.getItem("mi_paid") === "1"); } catch {} }, []);
  const setPaid = (v: boolean) => { setPaidState(v); try { localStorage.setItem("mi_paid", v ? "1" : "0"); } catch {} };
  const timer = useRef<any>(null);
  const after = useRef<any>(null);  // post-done refresh loop
  const { byKey } = usePlatforms();
  const coll = useApi("/collector");
  const sources: any[] = (coll.data?.connectors || []).filter((c: any) => c.enabled && c.supports_search && !SUPPLIER_NETWORKS.has(c.network));
  const savedNetworksApplied = useRef(false);
  useEffect(() => {
    if (!savedNetworksApplied.current && sp.get("saved_filter") && coll.data) {
      savedNetworksApplied.current = true;
      const want = (sp.get("network") || "").split(",").filter(Boolean);
      if (want.length) setOff(sources.filter((c) => !want.includes(c.network)).map((c) => c.adapter));
    }
  }, [coll.data, sp]);
  const isPaid = (c: any) => String(c.adapter).startsWith("apify");
  const active = sources.filter((c) => !off.includes(c.adapter) && (paid || !isPaid(c)));
  const sourceGroups = groupTikTokSources(sources);
  const progressGroups = groupTikTokProgress(job?.sources || {});
  // chip tắt = cũng lọc danh sách kết quả theo nền tảng (không chỉ bỏ nguồn khi quét)
  const nets = (l: any[]) => [...new Set(l.map((c) => c.network))];
  const picked = nets(sources.filter((c) => !off.includes(c.adapter)));
  const network = picked.length < nets(sources).length ? picked.join(",") : "";

  saved = { tags, exTags, submitted, scope, funnel, sort, videoOnly, minDays, maxAdv, jobIds, crossOnly, nameStatus, expand,
            mode, similarTo, category, language, minFit, minDemand, minEng };
  try { sessionStorage.setItem("mi_search", JSON.stringify(saved)); } catch { /* private mode: in-memory only */ }
  const exclude = exTags.join("|");  // chỉ tag đã chốt — tránh tải lại theo từng phím gõ
  const path = `/search${qs({ q: submitted, exclude, country: scope, funnel, sort, has_video: videoOnly, min_days: minDays, max_advertisers: maxAdv,
                             ids: jobIds, min_networks: crossOnly ? 2 : "", network, name_status: nameStatus === "ALL" ? "" : nameStatus,
                             expand: expand ? "" : "false", mode, similar_to: mode === "similar" ? similarTo : "", category, language,
                             min_fit: minFit, min_demand: minDemand, min_engagement: minEng })}`;
  const append = useRef(false);  // live-search products land at the end of the list: nothing on screen moves
  const dPath = useDebounced(path);  // typing in number boxes / switching filters fast → one request
  const { rows, total, loading, error, hasMore, loadMore, reload, last } = usePaged(dPath, 36, append);
  const reloadRef = useRef(reload);
  reloadRef.current = reload;  // poller always calls the reload bound to the current path (ids/filters)

  // products of a running search are added to the list in place (see usePaged `append`), no button to press
  const applyIds = (ids: string) => { if (ids !== jobIds) { append.current = jobIds !== "" && jobIds !== "0"; setJobIds(ids); } };

  const poll = (id: number) => {
    clearInterval(timer.current); clearInterval(after.current);
    let n = 0;
    timer.current = setInterval(async () => {
      try {
        const j = await api(`/search/jobs/${id}`);
        setJob(j);
        if (j.product_ids?.length) { pages.current[pageRef.current] = j.product_ids; applyIds(j.product_ids.join(",")); }
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
    if (!active.length) { setMsg("Chọn ít nhất một nguồn để tìm trực tiếp."); return; }
    const k = submit();
    if (!k.length) return;
    setMsg("");
    try {
      const r = await api("/search/live", { method: "POST", body: JSON.stringify({
        query: k.join("|"), countries: liveCountries(scope), user: getUser().name, track, limit, media_type: liveMedia || null, paid, expand: expandLive,
        adapters: active.map((c) => c.adapter) }) });  // always explicit: [] would mean "every source" to the API, supplier sites included
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
      <PageHeader title="Tìm sản phẩm" subtitle="Tìm trong kho, hoặc quét trực tiếp các thư viện quảng cáo đang bật (Meta Ad Library, TikTok Ads). Tìm giá nhập ở AliExpress / 1688 / Taobao thì dùng trang Nguồn hàng. Kết quả gộp theo sản phẩm." />
      <Card className="mb-4">
        <div className="flex flex-wrap items-center justify-between gap-2"><AdvertiserFilters /><Button variant="outline" size="sm" disabled={!submitted} onClick={async () => { try { await api("/workspace/searches", { method: "POST", body: JSON.stringify({ name: submitted.slice(0, 100), filters: { q: submitted, country: scope, funnel, sort, exclude, network, has_video: videoOnly, min_days: minDays, max_advertisers: maxAdv, min_networks: crossOnly ? 2 : "", name_status: nameStatus, expand, mode, category, language, min_fit: minFit, min_demand: minDemand, min_engagement: minEng } }) }); toast.success("Đã lưu bộ lọc vào tài khoản"); } catch (e: any) { toast.error(e.message); } }}>Lưu bộ lọc hiện tại</Button></div>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <ToggleGroup type="single" variant="outline" size="sm" spacing={1} value={mode || "all"} onValueChange={(v) => v && pickMode(v === "all" ? "" : v)} className="flex-wrap" aria-label="Chế độ tìm">
            {MODES.map(([k, l, tip]) => <ToggleGroupItem key={k || "all"} value={k || "all"} className="text-xs" title={tip}>{l}</ToggleGroupItem>)}
          </ToggleGroup>
          {mode === "similar" && (similarTo
            ? <Badge variant="secondary" className="gap-1">Tương tự sản phẩm #{similarTo}<button type="button" aria-label="Bỏ sản phẩm gốc" className="ml-1" onClick={() => setSimilarTo("")}>×</button></Badge>
            : <span className="text-xs text-muted-foreground">Dựa trên sản phẩm bạn đã lưu / yêu thích / test — bấm “SP tương tự” dưới một sản phẩm để chọn gốc khác.</span>)}
          {mode && <span className="text-xs text-muted-foreground">Không hiện sản phẩm MKT khác đã nhận. Search không tự nhận sản phẩm — bấm “Nhận” trên từng sản phẩm.</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          <TagInput tags={tags} setTags={setTags} draft={q} setDraft={setQ} onSubmit={(l) => (l ? live() : submit())}
                    placeholder="Nhiều từ khoá, Enter sau mỗi từ: fengshui ⏎ lucky bracelet ⏎ pixiu ⏎ (khớp BẤT KỲ từ nào)" />
          <GlobalImageSearch variant="outline" size="default" iconOnly preset={{ keywords: kws().join("|") || undefined, country: scope || undefined }} />
          <Button type="button" variant="outline" onClick={submit}>Tìm trong kho</Button>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <Button type="button" onClick={live} disabled={running || !kws().length || !active.length}>
                  {running ? <><Spinner /> Đang tìm trên nguồn…</> : <><Zap /> Tìm trực tiếp trên nguồn</>}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>{!kws().length ? "Nhập từ khoá trước" : !active.length ? "Chọn ít nhất một nguồn để quét" : `Quét ngay ${active.length} nguồn đang bật × ${kws().length} từ khoá (Ctrl+Enter)`}</TooltipContent>
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
          <Select value={nameStatus} onValueChange={setNameStatus}>
            <SelectTrigger size="sm" className="text-xs" aria-label="Trạng thái tên"><SelectValue /></SelectTrigger>
            <SelectContent>{NAME_FILTERS.map(([k, l]) => <SelectItem key={k} value={k}>{l}</SelectItem>)}</SelectContent>
          </Select>
          <Label className="text-xs" title="Tìm “vòng tay phong thủy” ra cả Feng Shui Bracelet, Lucky Bracelet… (vi · en · tl · ar · zh). Khớp đúng luôn đứng trước.">
            <Checkbox checked={expand} onCheckedChange={(v) => setExpand(v === true)} /> Đa ngôn ngữ / đồng nghĩa</Label>
          <Select value={category || "all"} onValueChange={(v) => setCategory(v === "all" ? "" : v)}>
            <SelectTrigger size="sm" className="text-xs" aria-label="Danh mục"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="all">Mọi danh mục</SelectItem>{(meta.data?.categories || []).filter((c: string) => c !== "non_product").map((c: string) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={language || "all"} onValueChange={(v) => setLanguage(v === "all" ? "" : v)}>
            <SelectTrigger size="sm" className="text-xs" aria-label="Ngôn ngữ quảng cáo"><SelectValue /></SelectTrigger>
            <SelectContent>{LANGS.map(([k, l]) => <SelectItem key={k || "all"} value={k || "all"}>{l}</SelectItem>)}</SelectContent>
          </Select>
          <Select value={minDemand || "any"} onValueChange={(v) => setMinDemand(v === "any" ? "" : v)}>
            <SelectTrigger size="sm" className="text-xs" aria-label="Nhu cầu thị trường"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="any">Mọi mức nhu cầu</SelectItem><SelectItem value="0.4">Nhu cầu ≥ trung bình</SelectItem><SelectItem value="0.6">Nhu cầu cao</SelectItem><SelectItem value="0.8">Nhu cầu rất cao</SelectItem></SelectContent>
          </Select>
          <Select value={minFit || "any"} onValueChange={(v) => { setMinFit(v === "any" ? "" : v); if (v !== "any" && sort !== "fit") setSort("fit"); }}>
            <SelectTrigger size="sm" className="text-xs" aria-label="Độ phù hợp"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="any">Mọi độ phù hợp</SelectItem><SelectItem value="40">Phù hợp ≥ 40</SelectItem><SelectItem value="60">Phù hợp ≥ 60</SelectItem><SelectItem value="80">Phù hợp ≥ 80</SelectItem></SelectContent>
          </Select>
          <Input className="h-8 w-48 text-xs" placeholder="Tương tác thực ≥ N / ad" inputMode="numeric" value={minEng} title="Chỉ sản phẩm có dữ liệu like / comment / share thật từ nguồn; để 0 = chỉ cần có dữ liệu"
                 onChange={(e) => setMinEng(e.target.value.replace(/\D/g, ""))} />
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
                <Label className="text-xs" title="VD “vòng tay phong thủy” → quét thêm “feng shui bracelet”, “lucky bracelet” (tối đa 2 từ khoá thêm / từ, mỗi từ = 1 lượt quét mọi nguồn)">
                  <Checkbox checked={expandLive} onCheckedChange={(v) => setExpandLive(v === true)} /> Quét thêm từ khoá tương đương (tiếng Anh + ngôn ngữ thị trường)</Label>
                <Label className="text-xs" title="Mỗi lượt quét Apify tốn tiền — chỉ bật khi cần"><Checkbox checked={paid} onCheckedChange={(v) => setPaid(v === true)} /> Dùng nguồn trả phí (Apify)</Label>
              </div>
            </CollapsibleContent>
          </Collapsible>
        </div>
        {sources.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
            <span className="mr-1 text-muted-foreground">Nguồn sẽ quét:</span>
            {sourceGroups.map((group) => {
              const available = group.connectors.filter((c) => paid || !isPaid(c));
              const runningSources = available.filter((c) => active.includes(c));
              const on = runningSources.length > 0;
              const grouped = group.key === "tiktok" && group.connectors.length > 1;
              const details = group.connectors.map((c) => `${c.name}${!paid && isPaid(c) ? " (cần bật nguồn trả phí)" : ""}`).join("\n");
              return (
                <Badge key={group.key} asChild variant="outline" className={cn("cursor-pointer px-1 py-0.5", on ? "border-series-1" : "opacity-45")}>
                  <button type="button" title={details} disabled={!available.length} aria-pressed={on}
                          aria-label={grouped ? `TikTok: ${runningSources.length}/${available.length} nguồn đang quét` : group.connectors[0].name}
                          onClick={() => setOff((previous) => {
                            const next = new Set(previous);
                            for (const c of group.connectors) {
                              if (on) next.add(c.adapter);
                              else next.delete(c.adapter);
                            }
                            return [...next];
                          })}>
                    <NetworkBadge network={group.network} byKey={byKey} small />
                    {grouped && <span className="text-[10px] text-muted-foreground">{runningSources.length}/{available.length} nguồn</span>}
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
                <TooltipContent className="max-w-xs">Gồm mọi nguồn & mọi nước đã quét. Thư viện quảng cáo lọc theo thị trường + đang chạy nên con số ở đó nhỏ hơn.</TooltipContent>
              </Tooltip>
              {!running && !job.has_more && job.found > 0 ? " — nguồn đã hết kết quả" : ""}
            </span>
            <span className="flex items-center gap-1">
              <Button variant="outline" size="xs" disabled={running || page === 0} onClick={() => goto(page - 1)}><ChevronLeft /> Trang trước</Button>
              <Button variant="outline" size="xs" disabled={running || (!job.has_more && !pages.current[page + 1])} onClick={next}
                      title="Trang sau = crawl mới tiếp từ chỗ dừng (chỉ metadata + ảnh bìa nhỏ). Nguồn Apify chỉ quét ở trang 1.">
                Trang sau <ChevronRight />
              </Button>
            </span>
            </AlertDescription>
          </Alert>
        )}
        {progressGroups.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
            {progressGroups.map((group) => {
              const fetched = group.entries.reduce((sum, [, s]) => sum + s.fetched, 0);
              const stored = group.entries.reduce((sum, [, s]) => sum + (s.stored ?? s.fetched), 0);
              const fresh = group.entries.reduce((sum, [, s]) => sum + s.new, 0);
              const failed = group.entries.reduce((sum, [, s]) => sum + s.failed, 0);
              const errors = group.entries.filter(([, s]) => s.error).length;
              return <Tooltip key={group.key}>
                <TooltipTrigger asChild>
                  <Badge variant="outline" className={cn("gap-1 px-2 py-0.5", errors && "border-critical")}>
                    <NetworkBadge network={group.network} byKey={byKey} small />
                    <span>{stored}{fresh ? ` · ${fresh} mới` : ""}{failed ? ` · ${failed} không lưu được` : ""}</span>
                    {errors > 0 && <span className="text-critical">{errors} nguồn lỗi</span>}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent className="max-w-sm space-y-1">
                  {group.entries.map(([name, s]) => <div key={name}>{name} — {s.error || `${s.fetched} dòng nhận về (gồm trùng)`}</div>)}
                  {group.entries.length > 1 && <div className="text-muted-foreground">Tổng {fetched} dòng từ {group.entries.length} nguồn; có thể trùng giữa các nguồn.</div>}
                </TooltipContent>
              </Tooltip>;
            })}
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
            {last?.off_topic > 0 && jobIds && (
              <div className="mb-2 flex flex-wrap items-center gap-3 rounded-md border bg-muted/40 px-3 py-2">
                <AlertTriangle className="size-4 shrink-0 text-warning" />
                <span className="text-sm">Có <b>{last.off_topic}</b> sản phẩm lệch từ khoá — nằm ở cuối danh sách, không lẫn vào kết quả.</span>
                <Button type="button" size="lg" variant={showOff ? "outline" : "default"}
                        onClick={() => { setShowOff(!showOff); if (!showOff) setTimeout(() => document.getElementById("off-topic")?.scrollIntoView({ behavior: "smooth" }), 150); }}>
                  {showOff ? "Ẩn sản phẩm lệch từ khoá" : `Hiện ${last.off_topic} sản phẩm lệch từ khoá`}
                </Button>
              </div>
            )}
            {total.toLocaleString("en-US")} sản phẩm{submitted ? ` khớp “${split(submitted).join("” hoặc “")}”` : ""}{exclude ? ` · loại trừ: ${split(exclude).join(", ")}` : ""}
            {expand && Object.entries(last?.understood || {}).filter(([, u]: any) => u.variants?.length).map(([k, u]: any) => (
              <div key={k} className="mt-0.5 text-xs text-muted-foreground" title="Từ khoá được hiểu là sản phẩm này, mọi ngôn ngữ">Hiểu “{k}” = {u.variants.slice(0, 8).join(", ")}{u.variants.length > 8 ? "…" : ""} <span className="opacity-70">({u.source === "llm" ? "AI, đã lưu" : "từ điển"})</span></div>
            ))}
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
                {rows.map((p: any) => <div key={p.id} className="flex flex-col gap-1.5"><ProductMediaCard p={p} /><PersonalBar p={p} onSimilar={findSimilar} onClaimed={reload} /></div>)}
              </div>
              <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
            </>
          )}
          {last?.off_topic > 0 && jobIds && (
            <div id="off-topic" className="mt-6 border-t pt-4">
              <Button type="button" size="lg" variant={showOff ? "outline" : "default"} className="w-full sm:w-auto" onClick={() => setShowOff(!showOff)}>
                {showOff ? "Ẩn sản phẩm lệch từ khoá" : `Hiện ${last.off_topic} sản phẩm lệch từ khoá`}
              </Button>
              {showOff && <OffTopic path={`/search${qs({ q: submitted, ids: jobIds, exclude, funnel, sort, expand: expand ? "" : "false", only_off: true })}`} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}

const ALLOC: Record<string, [string, "outline" | "secondary" | "destructive"]> = {
  available: ["Còn trống", "outline"], mine: ["Của bạn", "secondary"], taken: ["MKT khác đã nhận", "destructive"] };
// another MKT's ownership state (never who); kept apart from soft interest (Saved / Favorite elsewhere)
const TAKEN: Record<string, string> = { testing: "MKT khác đang test", tested: "MKT khác đang giữ (đã test)", reserved: "MKT khác đang giữ chỗ", claimed: "MKT khác đã nhận" };

/** Fit score + reasons, ownership, and the two per-product actions. Claiming is one explicit click per product. */
function PersonalBar({ p, onSimilar, onClaimed }: { p: any; onSimilar: (id: number) => void; onClaimed: () => void }) {
  const [alloc, setAlloc] = useState<string | undefined>(p.allocation);
  const [busy, setBusy] = useState(false);
  useEffect(() => setAlloc(p.allocation), [p.allocation]);
  const fit = p.fit;
  const reasons: string[] = [...(fit?.similar_reasons || []), ...(fit?.reasons || [])];
  const [base, variant] = ALLOC[alloc || ""] || [];
  const label = alloc === "taken" && p.taken_state ? TAKEN[p.taken_state] || base : base;
  if (!fit && !alloc) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 px-1 text-xs">
      {fit && fit.score != null && (
        <Tooltip>
          <TooltipTrigger asChild><Badge variant="secondary" className="cursor-help tabular-nums">Phù hợp {Math.round(fit.score)}</Badge></TooltipTrigger>
          <TooltipContent className="max-w-xs space-y-0.5">
            {fit.similarity != null && <div>Độ tương tự: {Math.round(fit.similarity)}</div>}
            {fit.coverage != null && fit.coverage < 1 && <div>Dữ liệu sản phẩm đủ {Math.round(fit.coverage * 100)}%</div>}
            {reasons.length ? reasons.map((r) => <div key={r}>• {r}</div>) : <div>Chưa có lý do nổi bật</div>}
          </TooltipContent>
        </Tooltip>
      )}
      {fit?.explore && <Badge variant="outline">Khám phá</Badge>}
      {label && <Badge variant={variant}>{label}</Badge>}
      {p.interest && alloc !== "mine" && <Badge variant="outline" title="MKT khác đã lưu / yêu thích sản phẩm này — vẫn có thể xem, lưu và nhận; gợi ý sẽ ưu tiên sản phẩm chưa ai quan tâm">{p.interest.label}</Badge>}
      {reasons[0] && <span className="min-w-0 flex-1 truncate text-muted-foreground" title={reasons.join(" · ")}>{reasons[0]}</span>}
      <span className="ml-auto flex gap-1">
        <Button type="button" size="xs" variant="ghost" onClick={() => onSimilar(p.id)} title="Chuyển Search sang chế độ Sản phẩm tương tự, lấy sản phẩm này làm gốc">SP tương tự</Button>
        {alloc === "available" && (
          <Button type="button" size="xs" variant="outline" disabled={busy} title="Giữ chỗ độc quyền sản phẩm này (48 giờ nếu chưa lưu / test)"
                  onClick={async () => {
                    setBusy(true);
                    try { await api("/allocations/claim", { method: "POST", body: JSON.stringify({ product_id: p.id }) }); setAlloc("mine"); toast.success("Đã giữ chỗ sản phẩm cho bạn"); onClaimed(); }
                    catch (e: any) {
                      const taken = String(e.message).includes("MKT");
                      toast.error(e.message, taken ? { action: { label: "Xem SP tương tự", onClick: () => onSimilar(p.id) } } : undefined);
                      if (taken) setAlloc("taken");
                    }
                    finally { setBusy(false); }
                  }}>{busy ? "Đang nhận…" : "Nhận"}</Button>
        )}
      </span>
    </div>
  );
}

/** Products the source returned but whose ad text never mentions the keyword: 20 at a time, below the real results. */
function OffTopic({ path }: { path: string }) {
  const { rows, total, loading, hasMore, loadMore } = usePaged(path, 20);
  return (
    <div className="mt-4">
      <div className="mb-3 text-sm text-ink2">Lệch từ khoá (nguồn khớp lỏng từng từ) — {total} sản phẩm</div>
      {rows.length === 0 && !loading ? <Empty>Không còn sản phẩm lệch từ khoá.</Empty> : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {rows.map((p: any) => <ProductMediaCard key={p.id} p={p} />)}
          </div>
          <LoadMore hasMore={hasMore} loading={loading} onMore={loadMore} shown={rows.length} total={total} />
        </>
      )}
    </div>
  );
}

const NAME_FILTERS: [string, string][] = [["ALL", "Mọi trạng thái tên"], ["UNKNOWN", "Chưa xác định sản phẩm (cần đặt tên)"],
  ["SUGGESTED", "Tên đề xuất (cần xác nhận)"], ["AUTO_CONFIRMED,VERIFIED", "Tên đã xác nhận"]];

export default function Page() {
  // SearchPage seeds its state from sessionStorage, which the server can't see: render it after mount only, or hydration mismatches
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return <Suspense fallback={<Loading />}>{mounted ? <SearchPage /> : <Loading />}</Suspense>;
}
