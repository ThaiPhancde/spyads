"use client";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useSession } from "@/components/workspace/session";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/components/layout/theme-toggle";

export function NavUser() {
  const { user } = useSession(); const [busy, setBusy] = useState(false);
  return <div className="space-y-2 rounded-lg border p-3 group-data-[collapsible=icon]:hidden"><Link href="/account" className="block truncate text-sm font-semibold">{user?.name}</Link><p className="truncate text-xs text-muted-foreground">{user?.email} · {user?.role}</p>
    <div className="flex items-center justify-between"><Button size="xs" variant="outline" disabled={busy} onClick={async () => { setBusy(true); try { await api("/auth/logout", { method: "POST" }); sessionStorage.removeItem("mi_search"); localStorage.removeItem("mi_user"); localStorage.removeItem("mi_team"); location.assign("/login/"); } catch (e: any) { toast.error(e.message); setBusy(false); } }}>Đăng xuất</Button><ThemeToggle /></div></div>;
}
