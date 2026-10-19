"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, qs, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { ProductMediaCard } from "@/components/media";
import { REGION_CHIPS } from "@/components/MarketPicker";
import { AdActions, AdvertiserFilters, FollowButton } from "@/components/workspace/actions";
import { Pager } from "@/components/workspace/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSession } from "@/components/workspace/session";

// Phase 8: the MKT's own Library — a view over their saves / favourites / tests / ownership, never a copy of the Ads
const SECTIONS: [string, string][] = [["my", "Thư viện của tôi"], ["saved", "Ads đã lưu"], ["favorite", "Yêu thích"], ["testing", "Đang test"],
  ["tested", "Đã test"], ["similar", "Gợi ý tương tự"], ["followed", "Đối thủ đang Follow"]];
const TESTS: [string, string][] = [["", "Mọi trạng thái test"], ["untested", "Chưa test"], ["testing", "Đang test"], ["tested", "Đã có kết quả"],
  ["winner", "Thành công"], ["loser", "Thất bại"], ["inconclusive", "Chưa kết luận"]];
const OUTCOME: Record<string, string> = { winner: "Thành công", loser: "Thất bại", inconclusive: "Chưa kết luận" };
const ALLOC: Record<string, string> = { reserved: "Đang giữ chỗ", claimed: "Đã nhận", testing: "Đang test", tested: "Đã test" };
const PAGE = 24;
const TAKEN: Record<string, string> = { testing: "MKT khác đang test", tested: "MKT khác đang giữ (đã test)", reserved: "MKT khác đang giữ chỗ", claimed: "MKT khác đã nhận" };
const all = (v: string) => v || "all";
const none = (v: string) => (v === "all" ? "" : v);

export default function Library() {
  const [section, setSection] = useState("my");
  useEffect(() => {  // deep link from the dashboard: /workspace/library?section=testing (after mount: no hydration mismatch)
    const s = new URLSearchParams(window.location.search).get("section");
    if (s && SECTIONS.some(([k]) => k === s)) setSection(s);
  }, []);
  const [offset, setOffset] = useState(0);
  const [category, setCategory] = useState("");
  const [market, setMarket] = useState("");
  const [ptype, setPtype] = useState("");
  const [test, setTest] = useState("");
  const [advertiser, setAdvertiser] = useState("");
  const [minFit, setMinFit] = useState("");
  const meta = useApi("/meta");
  const path = `/library${qs({ section, offset, limit: PAGE, category, market, product_type: ptype.trim(), test_status: test, advertiser_id: advertiser, min_fit: minFit })}`;
  const [dPath, setDPath] = useState(path);
  useEffect(() => { const t = setTimeout(() => setDPath(path), 300); return () => clearTimeout(t); }, [path]);  // typing a type: one request
  const { data, error, reload } = useApi(dPath);
  const { interactions } = useSession();
  const first = useRef(true);  // an ad saved / unsaved with the existing per-ad buttons → the Library list follows
  useEffect(() => { if (first.current) { first.current = false; return; } reload(); }, [interactions]);  // eslint-disable-line react-hooks/exhaustive-deps
  const follows = useApi("/library?section=followed&limit=1");  // advertiser list for the filter (own follows only)
  const reset = (f: () => void) => { f(); setOffset(0); };
  const counts = data?.counts || {};
  return <div>
    <PageHeader title="Thư viện của tôi" subtitle="Sản phẩm và Ads bạn đã lưu, yêu thích, đang test và đã test — chỉ bạn thấy. Thư viện tham chiếu Ads gốc, không sao chép." />
    <AdvertiserFilters />
    <div className="mb-3 flex flex-wrap gap-2">
      {SECTIONS.map(([k, l]) => <Button key={k} variant={section === k ? "default" : "outline"} onClick={() => reset(() => setSection(k))}>
        {l}{counts[k] != null ? <span className="ml-1 tabular-nums opacity-70">{counts[k]}</span> : null}</Button>)}
      <Button variant="ghost" onClick={reload}>Cập nhật</Button>
    </div>
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <Select value={all(category)} onValueChange={(v) => reset(() => setCategory(none(v)))}>
        <SelectTrigger size="sm" className="text-xs" aria-label="Danh mục"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="all">Mọi danh mục</SelectItem>{(meta.data?.categories || []).filter((c: string) => c !== "non_product").map((c: string) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
      </Select>
      <Select value={all(market)} onValueChange={(v) => reset(() => setMarket(none(v)))}>
        <SelectTrigger size="sm" className="text-xs" aria-label="Thị trường"><SelectValue /></SelectTrigger>
        <SelectContent>{REGION_CHIPS.map(([k, l]) => <SelectItem key={k || "all"} value={all(k)}>{k ? l : "Mọi thị trường"}</SelectItem>)}</SelectContent>
      </Select>
      <Input className="h-8 w-44 text-xs" placeholder="Loại sản phẩm (subcategory)" value={ptype} onChange={(e) => reset(() => setPtype(e.target.value))} aria-label="Loại sản phẩm" />
      <Select value={all(test)} onValueChange={(v) => reset(() => setTest(none(v)))}>
        <SelectTrigger size="sm" className="text-xs" aria-label="Trạng thái test"><SelectValue /></SelectTrigger>
        <SelectContent>{TESTS.map(([k, l]) => <SelectItem key={k || "all"} value={all(k)}>{l}</SelectItem>)}</SelectContent>
      </Select>
      <Select value={all(advertiser)} onValueChange={(v) => reset(() => setAdvertiser(none(v)))}>
        <SelectTrigger size="sm" className="text-xs" aria-label="Advertiser"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="all">Mọi advertiser</SelectItem>{(follows.data?.advertisers || []).map((a: any) => <SelectItem key={a.id} value={String(a.id)}>{a.name}</SelectItem>)}</SelectContent>
      </Select>
      <Select value={all(minFit)} onValueChange={(v) => reset(() => setMinFit(none(v)))}>
        <SelectTrigger size="sm" className="text-xs" aria-label="Độ phù hợp"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="all">Mọi độ phù hợp</SelectItem><SelectItem value="40">Phù hợp ≥ 40</SelectItem><SelectItem value="60">Phù hợp ≥ 60</SelectItem><SelectItem value="80">Phù hợp ≥ 80</SelectItem></SelectContent>
      </Select>
    </div>
    {section === "followed" && data?.advertisers && <Card className="mb-4" title="Advertiser bạn đang Follow">
      {data.advertisers.length ? <div className="flex flex-wrap gap-2">{data.advertisers.map((a: any) => <span key={a.id} className="inline-flex items-center gap-2 rounded-md border px-2 py-1 text-sm">
        <Link href={`/competitors/${a.id}`} className="underline">{a.name}</Link><span className="text-xs text-muted-foreground">{a.products} sản phẩm</span><FollowButton id={a.id} after={reload} /></span>)}</div>
        : <p className="text-sm text-muted-foreground">Chưa Follow advertiser nào. Follow từ card Ads hoặc trang Đối thủ.</p>}
    </Card>}
    {!data ? <Loading error={error} /> : <>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
        {data.rows.map((p: any) => <div key={p.id} className="flex flex-col gap-1.5"><ProductMediaCard p={p} /><LibraryBar p={p} onChange={reload} /></div>)}
      </div>
      {!data.rows.length && <p className="py-8 text-muted-foreground">{EMPTY[section]}</p>}
      <Pager offset={offset} total={data.total} size={PAGE} onChange={setOffset} />
    </>}
  </div>;
}

const EMPTY: Record<string, string> = {
  my: "Thư viện trống. Lưu hoặc yêu thích Ads ở Thư viện quảng cáo / Tìm sản phẩm, hoặc bấm “Nhận” một sản phẩm.",
  saved: "Chưa lưu Ads nào khớp bộ lọc.", favorite: "Chưa có sản phẩm yêu thích khớp bộ lọc.", testing: "Không có sản phẩm đang test.",
  tested: "Chưa có kết quả test nào.", similar: "Chưa có gợi ý — lưu, yêu thích hoặc test một sản phẩm để có gợi ý tương tự.",
  followed: "Advertiser bạn Follow chưa có sản phẩm nào còn trống.",
};

/** Personal score, state and every Library action for one product. Writes go through the shared endpoints. */
function LibraryBar({ p, onChange }: { p: any; onChange: () => void }) {
  const { refresh } = useSession();
  const [busy, setBusy] = useState(false);
  const L = p.library || {};
  const alloc = L.allocation;
  const fit = p.fit;
  const reasons: string[] = [...(fit?.similar_reasons || []), ...(fit?.reasons || [])];
  const run = async (fn: () => Promise<any>, ok: string) => {
    setBusy(true);
    try { await fn(); toast.success(ok); await refresh(); onChange(); } catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  };
  const post = (path: string, body?: any) => api(path, { method: "POST", body: JSON.stringify(body ?? {}) });
  return (
    <div className="space-y-1.5 px-1 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        {fit?.score != null && <Tooltip>
          <TooltipTrigger asChild><Badge variant="secondary" className="cursor-help tabular-nums">Phù hợp {Math.round(fit.score)}</Badge></TooltipTrigger>
          <TooltipContent className="max-w-xs space-y-0.5">
            {fit.similarity != null && <div>Độ tương tự: {Math.round(fit.similarity)}</div>}
            {fit.because_of?.length > 0 && <div>Vì bạn đã chọn: {fit.because_of.map((b: any) => b.name).join(", ")}</div>}
            {reasons.length ? reasons.map((r) => <div key={r}>• {r}</div>) : <div>Chưa có lý do nổi bật</div>}
          </TooltipContent>
        </Tooltip>}
        {L.saved && <Badge variant="outline">Đã lưu</Badge>}
        {L.favorite && <Badge variant="outline">Yêu thích</Badge>}
        {alloc && <Badge variant="secondary">{ALLOC[alloc.status] || alloc.status}</Badge>}
        {L.result && <Badge variant={L.result.outcome === "winner" ? "secondary" : "outline"}>Kết quả: {OUTCOME[L.result.outcome]}</Badge>}
        {!alloc && p.ownership === "taken" && <Badge variant="destructive">{TAKEN[p.taken_state] || "MKT khác đã nhận"}</Badge>}
        {p.interest && <Badge variant="outline" title="MKT khác đã lưu / yêu thích sản phẩm này — không ai biết là ai">{p.interest.label}</Badge>}
        {!alloc && p.ownership === "available" && <Badge variant="outline">Còn trống</Badge>}
        {reasons[0] && <span className="min-w-0 flex-1 truncate text-muted-foreground" title={reasons.join(" · ")}>{reasons[0]}</span>}
      </div>
      <div className="flex flex-wrap gap-1">
        <Button size="xs" variant={L.saved ? "secondary" : "ghost"} disabled={busy} aria-pressed={!!L.saved}
                onClick={() => run(() => post(`/library/products/${p.id}/save`, { on: !L.saved }), L.saved ? "Đã bỏ lưu" : "Đã lưu sản phẩm")}>{L.saved ? "✓ Đã lưu" : "Lưu"}</Button>
        <Button size="xs" variant={L.favorite ? "secondary" : "ghost"} disabled={busy} aria-pressed={!!L.favorite}
                onClick={() => run(() => post(`/library/products/${p.id}/favorite`, { on: !L.favorite }), L.favorite ? "Đã bỏ yêu thích" : "Đã thêm yêu thích")}>{L.favorite ? "✓ Yêu thích" : "Yêu thích"}</Button>
        {p.ownership !== "taken" && alloc?.status !== "testing" &&
          <Button size="xs" variant="outline" disabled={busy} title={alloc ? "Chuyển sản phẩm sang đang test" : "Nhận sản phẩm và bắt đầu test"}
                  onClick={async () => {
                    setBusy(true);
                    try { await post(`/library/products/${p.id}/start-test`); toast.success("Đã bắt đầu test"); await refresh(); onChange(); }
                    catch (e: any) {  // taken meanwhile: say so and point at open look-alikes
                      toast.error(e.message, String(e.message).includes("MKT") ? { action: { label: "Xem SP tương tự",
                        onClick: () => window.location.assign(`/search?saved_filter=library&mode=similar&similar_to=${p.id}&sort=fit`) } } : undefined);
                      onChange();
                    } finally { setBusy(false); }
                  }}>Bắt đầu test</Button>}
        {alloc && <Button size="xs" variant="ghost" disabled={busy} title="Trả sản phẩm về kho chung: MKT khác có thể nhận. Lịch sử và kết quả test vẫn giữ."
                          onClick={() => { if (confirm("Trả sản phẩm này về kho chung?")) run(() => post(`/allocations/${alloc.id}/release`), "Đã trả sản phẩm về kho"); }}>Trả về kho</Button>}
      </div>
      {alloc && <ResultForm allocationId={alloc.id} disabled={busy} onSaved={() => run(async () => {}, "Đã cập nhật kết quả test")} />}
      <SimilarInline id={p.id} onSaved={onChange} />
      {L.ads?.length > 0 && <Collapsible>
        <CollapsibleTrigger asChild><Button size="xs" variant="link" className="h-auto px-0">Ads của tôi ({L.ads.length})</Button></CollapsibleTrigger>
        <CollapsibleContent className="mt-1 space-y-2">
          {L.ads.map((a: any) => <div key={a.id} className="rounded-md border p-2">
            <div className="font-medium">{a.title || `Ads #${a.id}`}</div>
            <p className="my-1 line-clamp-3 text-muted-foreground">{a.text}</p>
            <AdActions id={a.id} /><FollowButton id={a.advertiser_id} />
            {a.snapshot_url && <a className="mt-1 block underline" href={a.snapshot_url} target="_blank" rel="noreferrer">Xem Ads gốc</a>}
            <form className="mt-2 space-y-1" onSubmit={async (e) => { e.preventDefault(); const note = new FormData(e.currentTarget).get("note"); try { await api(`/workspace/ads/${a.id}`, { method: "PUT", body: JSON.stringify({ note }) }); toast.success("Đã lưu ghi chú"); } catch (err: any) { toast.error(err.message); } }}>
              <textarea aria-label="Ghi chú riêng" name="note" defaultValue={a.note} maxLength={3000} placeholder="Ghi chú riêng…" className="w-full rounded border bg-background p-2 text-sm" />
              <Button size="xs" variant="outline">Lưu ghi chú</Button>
            </form>
          </div>)}
        </CollapsibleContent>
      </Collapsible>}
    </div>
  );
}

function ResultForm({ allocationId, disabled, onSaved }: { allocationId: number; disabled: boolean; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [outcome, setOutcome] = useState("winner");
  return <Collapsible open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger asChild><Button size="xs" variant="link" className="h-auto px-0">Cập nhật kết quả test</Button></CollapsibleTrigger>
    <CollapsibleContent>
      <form className="mt-1 flex flex-wrap items-center gap-1.5 rounded-md border p-2" onSubmit={async (e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        const num = (k: string) => (f.get(k) ? Number(f.get(k)) : null);
        try {
          await api(`/allocations/${allocationId}/results`, { method: "POST", body: JSON.stringify({ outcome, market: (f.get("market") as string) || null,
            spend: num("spend"), orders: num("orders"), revenue: num("revenue"), note: f.get("note") || "" }) });
          setOpen(false); onSaved();
        } catch (err: any) { toast.error(err.message); }
      }}>
        <Select value={outcome} onValueChange={setOutcome}>
          <SelectTrigger size="sm" className="text-xs" aria-label="Kết quả"><SelectValue /></SelectTrigger>
          <SelectContent>{Object.entries(OUTCOME).map(([k, l]) => <SelectItem key={k} value={k}>{l}</SelectItem>)}</SelectContent>
        </Select>
        <Input name="market" className="h-8 w-20 text-xs" placeholder="Thị trường" maxLength={8} aria-label="Thị trường test" />
        <Input name="spend" className="h-8 w-20 text-xs" placeholder="Chi phí" inputMode="decimal" aria-label="Chi phí" />
        <Input name="orders" className="h-8 w-16 text-xs" placeholder="Đơn" inputMode="numeric" aria-label="Số đơn" />
        <Input name="revenue" className="h-8 w-24 text-xs" placeholder="Doanh thu" inputMode="decimal" aria-label="Doanh thu" />
        <Input name="note" className="h-8 min-w-32 flex-1 text-xs" placeholder="Ghi chú" maxLength={3000} aria-label="Ghi chú kết quả" />
        <Button size="xs" disabled={disabled}>Lưu kết quả</Button>
      </form>
    </CollapsibleContent>
  </Collapsible>;
}

/** Similar products right inside the Library: only ones still open to claim (exclusive view of Phase 6). */
function SimilarInline({ id, onSaved }: { id: number; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const { data, error } = useApi(open ? `/similar/products/${id}?exclusive=true&limit=6` : null);
  return <Collapsible open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger asChild><Button size="xs" variant="link" className="h-auto px-0">Sản phẩm tương tự</Button></CollapsibleTrigger>
    <CollapsibleContent className="mt-1">
      {!data ? <span className="text-muted-foreground">{error || "Đang tìm…"}</span> : data.similar.length === 0 ? <span className="text-muted-foreground">Chưa có sản phẩm tương tự còn trống.</span> :
        <ul className="space-y-1">{data.similar.map((s: any) => <li key={s.product_id} className="flex items-center gap-2">
          <Badge variant="outline" className="tabular-nums">{Math.round(s.score)}</Badge>
          <Link href={`/products/${s.product_id}`} className="min-w-0 flex-1 truncate underline" title={s.reasons.join(" · ")}>{s.name}</Link>
          <Button size="xs" variant="ghost" onClick={async () => { try { await api(`/library/products/${s.product_id}/save`, { method: "POST", body: JSON.stringify({ on: true }) }); toast.success("Đã lưu vào thư viện"); onSaved(); } catch (e: any) { toast.error(e.message); } }}>Lưu</Button>
        </li>)}</ul>}
    </CollapsibleContent>
  </Collapsible>;
}
