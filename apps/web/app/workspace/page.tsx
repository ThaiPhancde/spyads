"use client";
import Link from "next/link";
import { fmt, useApi } from "@/lib/api";
import { Card, Loading, PageHeader, Stat } from "@/components/ui";
import { useSession } from "@/components/workspace/session";
import { EVENT_LABELS, STATUS_LABELS } from "@/components/workspace/common";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const OUTCOME: Record<string, string> = { winner: "Thành công", loser: "Thất bại", inconclusive: "Chưa kết luận" };
const LISTS: [string, string, string][] = [["saved", "Đã lưu", "saved"], ["favorite", "Yêu thích", "favorite"], ["claimed", "Đã nhận", "my"],
  ["testing", "Đang test", "testing"], ["test_success", "Test thành công", "tested"], ["test_fail", "Test thất bại", "tested"]];

export default function Workspace() {
  const { user } = useSession();
  const { data, error, reload } = useApi("/workspace/dashboard");
  const me = useApi("/library/dashboard");  // Phase 9: personal numbers — products = Product Clusters, ads = ads
  if (!data) return <Loading error={error} />;
  const d = me.data;
  const P = d?.kpis.products || {};
  const A = d?.kpis.ads || {};
  return <div className="space-y-5"><PageHeader title={`Không gian của ${user?.name || "MKT"}`} subtitle="Đối thủ, quảng cáo, sản phẩm và gợi ý riêng của bạn"><Button variant="outline" onClick={() => { reload(); me.reload(); }}>Cập nhật</Button></PageHeader>
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Stat label="Đối thủ đang Follow" value={data.followed} /><Stat label="Đối thủ có hoạt động · 24h" value={data.active_competitors} /><Stat label="Ads mới · 24h" value={data.new_ads_24h} /><Stat label="Thông báo chưa đọc" value={data.unread} /></div>
    <div className="flex flex-wrap gap-3">{[["/competitors", "Đối thủ của tôi"], ["/workspace/library", "Thư viện của tôi"], ["/workspace/notifications", "Thông báo"], ["/workspace/searches", "Bộ lọc đã lưu"], ["/workspace/activity", "Lịch sử của tôi"]].map(([href, label]) => <Button key={href} variant="outline" asChild><Link href={href}>{label}</Link></Button>)}</div>

    {!d ? <Loading error={me.error} /> : <>
      <div>
        <h2 className="mb-2 text-sm font-semibold">Sản phẩm của tôi <span className="font-normal text-muted-foreground">(đếm theo Product Cluster)</span></h2>
        <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <Stat label="Được đề xuất" value={fmt.n(P.recommended)} sub={`${P.test_candidates} TEST / TEST_NOW trong top`} />
          <Stat label="Đã lưu" value={fmt.n(P.saved)} /><Stat label="Yêu thích" value={fmt.n(P.favorite)} />
          <Stat label="Đã nhận" value={fmt.n(P.claimed)} sub="giữ chỗ + đã nhận" /><Stat label="Đang test" value={fmt.n(P.testing)} />
          <Stat label="Test thành công / thất bại" value={`${P.test_success} / ${P.test_fail}`} sub={`${P.test_inconclusive} chưa kết luận`} />
        </div>
      </div>
      <div>
        <h2 className="mb-2 text-sm font-semibold">Ads của tôi <span className="font-normal text-muted-foreground">(đếm theo từng quảng cáo)</span></h2>
        <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-5">
          <Stat label="Ads đã lưu" value={fmt.n(A.saved)} /><Stat label="Ads yêu thích" value={fmt.n(A.favorite)} /><Stat label="Ads đánh dấu đã test" value={fmt.n(A.marked_tested)} />
          <Stat label="Ads mới từ đối thủ · 24h" value={fmt.n(A.new_from_followed_24h)} /><Stat label="Ads mới từ đối thủ · 7 ngày" value={fmt.n(A.new_from_followed_7d)} />
        </div>
      </div>
      {(d.cold_start || d.is_default_profile) && <p className="text-sm text-muted-foreground">Hồ sơ mới: đề xuất đang dựa trên thị trường ưu tiên và tín hiệu Ads thật. Lưu, yêu thích hoặc test sản phẩm để gợi ý sát với bạn hơn.</p>}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Sản phẩm được đề xuất" action={<Link className="text-xs underline" href="/search?saved_filter=dashboard&mode=for_me&sort=fit">Xem tất cả</Link>}>
          {!d.recommended.length && <Empty>Chưa có đề xuất.</Empty>}
          {d.recommended.map((p: any) => <ProductRow key={p.id} p={p} right={<><Badge variant="secondary" className="tabular-nums">Phù hợp {Math.round(p.score)}</Badge>{p.explore && <Badge variant="outline">Khám phá</Badge>}</>} note={p.reasons?.[0]} />)}
        </Card>
        <Card title="Ads của sản phẩm được đề xuất">
          {!d.recommended_ads.length && <Empty>Chưa có Ads đang chạy cho các đề xuất.</Empty>}
          {d.recommended_ads.map((a: any) => <AdRow key={a.id} a={a} />)}
        </Card>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {LISTS.map(([k, title, section]) => <Card key={k} title={`${title} · ${P[k] ?? 0}`} action={<Link className="text-xs underline" href={`/workspace/library?section=${section}`}>Thư viện</Link>}>
          {!d.lists[k]?.length && <Empty>Chưa có sản phẩm.</Empty>}
          {(d.lists[k] || []).map((p: any) => <ProductRow key={p.id} p={p}
            right={p.result ? <Badge variant={p.result.outcome === "winner" ? "secondary" : "outline"}>{OUTCOME[p.result.outcome]}</Badge> : p.allocation ? <Badge variant="outline">{p.allocation.status}</Badge> : null} />)}
        </Card>)}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`Đối thủ đang Follow · ${d.kpis.followed_advertisers}`}>
          {!d.followed.length && <Empty>Follow đối thủ để theo dõi Ads mới.</Empty>}
          {d.followed.map((a: any) => <Link key={a.id} href={`/competitors/${a.id}`} className="flex justify-between border-b py-2 text-sm"><span className="underline">{a.name}</span><span className="text-muted-foreground">{a.products} sản phẩm</span></Link>)}
        </Card>
        <Card title="Ads mới từ đối thủ">
          {!d.new_ads.length && <Empty>Chưa có Ads của đối thủ bạn Follow.</Empty>}
          {d.new_ads.map((a: any) => <AdRow key={a.id} a={a} />)}
        </Card>
        <Card title="Danh mục quan tâm nhất">
          {!d.top_categories.length && !d.manual_categories.length && <Empty>Chưa đủ dữ liệu — hệ thống học từ Ads bạn lưu, yêu thích và test.</Empty>}
          {d.manual_categories.map((c: string) => <div key={c} className="flex justify-between border-b py-2 text-sm"><span>{c}</span><Badge variant="outline">bạn chọn</Badge></div>)}
          {d.top_categories.map((c: any) => <div key={c.key} className="border-b py-2 text-sm"><div className="flex justify-between"><span>{c.key}</span><span className="tabular-nums text-muted-foreground">{Math.round(c.score * 100)}</span></div>
            <div className="mt-1 h-1.5 rounded bg-muted"><div className="h-1.5 rounded bg-primary" style={{ width: `${Math.round(c.score * 100)}%` }} /></div></div>)}
          <p className="mt-2 text-xs text-muted-foreground">Học từ Ads bạn lưu, yêu thích, test và đối thủ bạn Follow.</p>
        </Card>
      </div>
      <Card title="Sản phẩm tương tự được đề xuất" action={<Link className="text-xs underline" href="/search?saved_filter=dashboard&mode=similar&sort=fit">Xem thêm</Link>}>
        {!d.similar.length && <Empty>Lưu, yêu thích hoặc test sản phẩm để nhận gợi ý tương tự.</Empty>}
        <div className="grid gap-x-6 md:grid-cols-2">{d.similar.map((p: any) => <ProductRow key={p.id} p={p} right={<Badge variant="secondary" className="tabular-nums">Tương tự {Math.round(p.score)}</Badge>}
          note={p.because_of?.length ? `Vì bạn đã chọn: ${p.because_of.map((b: any) => b.name).join(", ")}` : p.reasons?.[0]} />)}</div>
      </Card>
    </>}

    <div className="grid gap-4 lg:grid-cols-2"><Card title="Thay đổi quảng cáo gần đây">{!data.recent.length && <p className="text-sm text-muted-foreground">Chưa có thay đổi mới trong 24 giờ.</p>}{data.recent.map((e: any) => <Link href={`/competitors/${e.advertiser_id}`} key={e.id} className="block border-b py-3 text-sm"><b>{EVENT_LABELS[e.kind] || e.kind}</b><p>{(e.data.fields || []).join(", ")}</p><span className="text-muted-foreground">{fmt.dt(e.at)}</span></Link>)}</Card>
    <Card title="Lần kiểm tra gần nhất">{!data.sources.length && <p className="text-sm text-muted-foreground">Follow đối thủ để bắt đầu theo dõi.</p>}{data.sources.map((s: any) => <Link key={s.advertiser_id} href={`/competitors/${s.advertiser_id}`} className="block border-b py-3 text-sm">Đối thủ #{s.advertiser_id} · {STATUS_LABELS[s.source_status]}<p className="text-muted-foreground">{fmt.dt(s.last_checked_at)}</p></Link>)}</Card></div>
  </div>;
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function ProductRow({ p, right, note }: { p: any; right?: React.ReactNode; note?: string }) {
  return <div className="border-b py-2 text-sm">
    <div className="flex items-center gap-2">
      <Link href={`/products/${p.id}`} className="min-w-0 flex-1 truncate underline" title={p.name}>{p.name}</Link>
      {p.recommendation && ["TEST", "TEST_NOW"].includes(p.recommendation) && <Badge variant="outline">{p.recommendation}</Badge>}
      {right}
    </div>
    <div className="text-xs text-muted-foreground">{[p.category, p.country].filter(Boolean).join(" · ")}{note ? ` — ${note}` : ""}</div>
  </div>;
}

function AdRow({ a }: { a: any }) {
  return <div className="border-b py-2 text-sm">
    <div className="flex items-center gap-2"><span className="min-w-0 flex-1 truncate font-medium">{a.title || `Ads #${a.id}`}</span>
      {a.snapshot_url && <a className="text-xs underline" href={a.snapshot_url} target="_blank" rel="noreferrer">Ads gốc</a>}</div>
    <div className="text-xs text-muted-foreground">{a.advertiser ? `${a.advertiser} · ` : ""}{a.country || ""}{a.first_seen_at ? ` · ${fmt.dt(a.first_seen_at)}` : ""}
      {a.product_id ? <> · <Link className="underline" href={`/products/${a.product_id}`}>sản phẩm</Link></> : null}</div>
  </div>;
}
