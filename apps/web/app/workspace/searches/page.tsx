"use client";
import Link from "next/link";
import { api, qs, useApi } from "@/lib/api";
import { Card, Loading, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
export default function Searches() {
  const { data, error, reload } = useApi("/workspace/searches");
  return <div className="space-y-3"><PageHeader title="Bộ lọc tìm kiếm đã lưu" subtitle="Lưu bộ lọc từ trang Tìm sản phẩm để dùng lại trong những lần đăng nhập sau." />{!data ? <Loading error={error} /> : data.rows.map((s: any) => <Card key={s.id}><div className="flex items-center justify-between gap-3"><Link href={`/search${qs({ ...s.filters, saved_filter: s.id })}`} className="font-medium underline">{s.name}</Link><Button variant="ghost" size="sm" onClick={async () => { try { await api(`/workspace/searches/${s.id}`, { method: "DELETE" }); reload(); } catch (e: any) { toast.error(e.message); } }}>Xóa</Button></div><p className="text-xs text-muted-foreground">{s.filters.q} · {s.filters.country}</p></Card>)}{data && !data.rows.length && <p className="text-sm text-muted-foreground">Chưa có bộ lọc đã lưu.</p>}</div>;
}
