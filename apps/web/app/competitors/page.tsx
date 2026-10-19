"use client";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { api, fmt, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FollowButton } from "@/components/workspace/actions";
import { Pager, STATUS_LABELS } from "@/components/workspace/common";

export default function Competitors() {
  const [browse, setBrowse] = useState(false); const [q, setQ] = useState(""); const [term, setTerm] = useState(""); const [offset, setOffset] = useState(0);
  const { data, error, reload } = useApi(`${browse ? `/advertisers?q=${encodeURIComponent(term)}` : "/competitors?"}&offset=${offset}`);
  const [busy, setBusy] = useState<number | null>(null);
  return <div className="space-y-4"><PageHeader title="Đối thủ của tôi" subtitle="Follow được lưu đến khi bạn bỏ theo dõi. Lịch kiểm tra chạy ngay cả khi bạn đăng xuất.">
    <Button variant="outline" onClick={() => { setBrowse(!browse); setOffset(0); }}>{browse ? "Đang theo dõi" : "Tìm advertiser để Follow"}</Button><Button variant="ghost" onClick={reload}>Cập nhật</Button></PageHeader>
    {browse && <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setTerm(q); setOffset(0); }}><Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tên hoặc ID advertiser trong kho ToolSpy" /><Button>Tìm</Button></form>}
    {!data ? <Loading error={error} /> : <><div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{data.rows.map((a: any) => <Card key={a.id} title={<Link href={`/competitors/${a.id}`} className="hover:underline">{a.name}</Link>}>
      {a.avatar_url && <img alt="" src={a.avatar_url} className="mb-2 size-10 rounded-full object-cover" loading="lazy" />}<p className="text-sm text-muted-foreground">{a.platform || "Chưa rõ nền tảng"} · {a.country || "Chưa rõ thị trường"} · {a.source || "Nguồn cũ"}</p>
      {!browse && <div className="my-3 grid grid-cols-2 gap-2 text-sm"><span><b>{a.total_ads}</b> Ads đã thu thập</span><span><b>{a.new_ads}</b> Ads mới chưa xem</span><span className="col-span-2">{STATUS_LABELS[a.source_status] || a.source_status} · mỗi {a.interval_hours}h</span><span className="col-span-2 text-xs text-muted-foreground">Thành công gần nhất: {fmt.dt(a.last_success_at)}</span></div>}
      {!browse && a.source_status === "success" && a.last_changes === 0 && <p className="my-2 text-sm">Không phát hiện thay đổi</p>}
      <p className="my-3 text-xs text-muted-foreground">{a.capability.detail}</p>{a.error && <p className="mb-3 text-xs text-amber-700 dark:text-amber-400">{a.error}</p>}
      <div className="flex flex-wrap gap-2"><Button size="sm" variant="outline" asChild><Link href={`/competitors/${a.id}`}>Xem chi tiết</Link></Button><FollowButton id={a.id} after={reload} />
      {!browse && <Button size="sm" variant="outline" disabled={!a.capability.supported || busy === a.id || a.source_status === "running"} onClick={async () => { setBusy(a.id); try { await api(`/competitors/${a.id}/refresh`, { method: "POST" }); toast.success("Đã đưa vào lịch kiểm tra"); reload(); } catch (e: any) { toast.error(e.message); } finally { setBusy(null); } }}>Kiểm tra ngay</Button>}</div>
    </Card>)}</div>{!data.rows.length && <Card><p className="text-muted-foreground">{browse ? "Không tìm thấy advertiser." : "Bạn chưa Follow đối thủ nào. Tìm advertiser hoặc bấm Follow trên thẻ quảng cáo."}</p></Card>}<Pager offset={offset} total={data.total} onChange={setOffset} /></>}
    <Link className="text-sm underline text-muted-foreground" href="/competitor-radar">Xem Radar toàn thị trường</Link>
  </div>;
}
