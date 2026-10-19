"use client";
import { createContext, useContext, useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { setUser } from "@/lib/realtime";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { SiteHeader } from "@/components/layout/site-header";
import { RealtimeToasts } from "@/components/layout/realtime-toasts";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Button } from "@/components/ui/button";

type User = { id: number; name: string; email: string; role: "MKT" };
type Context = { user: User | null; profile: any; followed: number[]; interactions: Record<number, any>; refresh: () => Promise<void>; reload: () => Promise<void> };
const Session = createContext<Context>({ user: null, profile: {}, followed: [], interactions: {}, refresh: async () => {}, reload: async () => {} });
export const useSession = () => useContext(Session);
const publicPaths = ["/login", "/register", "/forgot-password", "/reset-password"];

export function SessionShell({ children }: { children: React.ReactNode }) {
  const path = usePathname().replace(/\/$/, "");
  const publicPage = publicPaths.includes(path);
  const router = useRouter();
  const [user, setAccount] = useState<User | null>(null);
  const [profile, setProfile] = useState<any>({});
  const [workspace, setWorkspace] = useState<any>({ followed_ids: [], interactions: {} });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const refresh = async () => { const d = await api("/workspace/state"); setWorkspace(d); };
  const reload = async () => {
    setLoading(true); setError("");
    try {
      const response = await fetch("/api/auth/me", { credentials: "same-origin", cache: "no-store" });
      if (response.status === 401) { setAccount(null); if (!publicPage) router.replace("/login/"); return; }
      if (!response.ok) throw new Error("Không kết nối được API tài khoản");
      const d = await response.json(); setAccount(d.user); setProfile(d.profile); setUser(d.user.name, d.profile.team);
      await refresh();
    } catch (e: any) { setError(e.message); } finally { setLoading(false); }
  };
  useEffect(() => { reload(); }, []);
  useEffect(() => { if (!loading && !user && !publicPage && !error) router.replace("/login/"); }, [path, loading, user, error]);
  if (publicPage) return <div className="flex min-h-screen items-center justify-center bg-muted/30 p-5">{children}</div>;
  if (loading) return <div className="p-8 text-muted-foreground">Đang tải không gian làm việc…</div>;
  if (error) return <div className="p-8"><p role="alert">{error}</p><Button onClick={reload}>Thử lại</Button></div>;
  if (!user) return <div className="p-8">Đang chuyển đến đăng nhập…</div>;
  return <Session.Provider value={{ user, profile, followed: workspace.followed_ids, interactions: workspace.interactions, refresh, reload }}>
    <SidebarProvider style={{ "--sidebar-width": "16.5rem", "--header-height": "3rem" } as React.CSSProperties}>
      <AppSidebar variant="inset" /><SidebarInset><SiteHeader /><main className="flex min-w-0 flex-1 flex-col gap-4 p-4 md:p-6">{children}</main></SidebarInset>
    </SidebarProvider><RealtimeToasts />
  </Session.Provider>;
}
