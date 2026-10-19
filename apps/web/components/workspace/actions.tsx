"use client";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { api, useApi } from "@/lib/api";
import { track } from "@/lib/track";
import { Button } from "@/components/ui/button";
import { useSession } from "./session";

export function FollowButton({ id, after }: { id?: number | null; after?: () => void }) {
  const { followed, refresh } = useSession(); const [busy, setBusy] = useState(false);
  if (!id) return null;
  const active = followed.includes(id);
  return <Button type="button" size="sm" variant={active ? "secondary" : "outline"} disabled={busy} title={active ? "Bỏ theo dõi đối thủ" : "Theo dõi advertiser trong tài khoản của bạn"} onClick={async (e) => {
    e.preventDefault(); e.stopPropagation(); setBusy(true);
    try { await api(`/competitors/${id}/follow`, { method: active ? "DELETE" : "POST", ...(active ? {} : { body: JSON.stringify({ interval_hours: 24 }) }) }); await refresh(); after?.(); }
    catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
  }}>{busy ? "Đang lưu…" : active ? "Đang theo dõi · Bỏ theo dõi" : "+ Follow đối thủ"}</Button>;
}

export function AdActions({ id }: { id: number }) {
  const { interactions, refresh } = useSession(); const [busy, setBusy] = useState(false); const state = interactions[id] || {};
  return <div className="flex flex-wrap gap-1">{([ ["saved", "Lưu"], ["favorite", "Yêu thích"], ["tested", "Đã test"] ] as const).map(([key, label]) =>
    <Button type="button" size="xs" variant={state[key] ? "secondary" : "ghost"} key={key} disabled={busy} aria-pressed={!!state[key]} onClick={async () => {
      setBusy(true); try { await api(`/workspace/ads/${id}`, { method: "PUT", body: JSON.stringify({ [key]: !state[key] }) }); await refresh(); }
      catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
    }}>{state[key] ? "✓ " : ""}{label}</Button>)}
    <Button type="button" size="xs" variant="ghost" title="Bỏ qua Ads này (ghi vào lịch sử của bạn)" onClick={() => { track("ad_skip", [id]); toast("Đã bỏ qua"); }}>Bỏ qua</Button>
    <Button type="button" size="xs" variant="ghost" title="Không quan tâm sản phẩm/Ads này" onClick={() => { track("ad_not_interested", [id]); toast("Đã ghi nhận: không quan tâm"); }}>Không quan tâm</Button></div>;
}

export function AdvertiserFilters() {
  const { data, reload } = useApi("/workspace/filters"); const [busy, setBusy] = useState(false);
  return <div className="my-3 flex flex-wrap items-center gap-2 text-sm"><label htmlFor="advertiser-scope">Advertiser</label>
    <select id="advertiser-scope" className="h-8 rounded-md border bg-background px-2" disabled={!data || busy} value={data?.mode || "all"} onChange={async (e) => {
      setBusy(true); try { await api("/workspace/filters", { method: "PUT", body: JSON.stringify({ mode: e.target.value }) }); reload(); window.dispatchEvent(new Event("workspace-filters")); }
      catch (e: any) { toast.error(e.message); } finally { setBusy(false); }
    }}><option value="all">Tất cả, loại trừ blacklist</option><option value="followed">Chỉ đang Follow</option><option value="whitelist">Chỉ Whitelist</option></select>
    <Link className="text-xs underline text-muted-foreground" href="/workspace/filters">Quản lý Whitelist / Blacklist</Link></div>;
}
