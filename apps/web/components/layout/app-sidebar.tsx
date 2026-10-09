"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { ScanSearch } from "lucide-react";
import { NAV, isActive } from "@/components/layout/nav";
import { NavUser } from "@/components/layout/nav-user";
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu,
  SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, useSidebar,
} from "@/components/ui/sidebar";
import { api } from "@/lib/api";
import { useEvents } from "@/lib/realtime";

export function AppSidebar(props: React.ComponentProps<typeof Sidebar>) {
  const path = usePathname();
  const { isMobile, setOpenMobile } = useSidebar();
  const [unread, setUnread] = useState(0);
  useEffect(() => { api("/alerts?limit=1").then((d) => setUnread(d.unread)).catch(() => {}); }, [path]);
  useEvents((e) => { if (e.type === "ALERT") setUnread((n) => n + 1); });
  useEffect(() => { if (isMobile) setOpenMobile(false); }, [path, isMobile, setOpenMobile]);

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton asChild size="lg" className="data-[slot=sidebar-menu-button]:p-1.5!">
              <Link href="/search">
                <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
                  <ScanSearch className="size-4" />
                </div>
                <div className="grid flex-1 text-left leading-tight">
                  <span className="truncate text-sm font-semibold">ToolSpy</span>
                  <span className="truncate text-[11px] text-muted-foreground">Market Intelligence OS</span>
                </div>
              </Link>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {NAV.map((g) => (
          <SidebarGroup key={g.group}>
            <SidebarGroupLabel>{g.group}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {g.items.map((it) => (
                  <SidebarMenuItem key={it.href}>
                    <SidebarMenuButton asChild isActive={isActive(it.href, path)} tooltip={it.hint ? `${it.label} — ${it.hint}` : it.label}>
                      <Link href={it.href}>
                        <it.icon />
                        <span>{it.label}</span>
                      </Link>
                    </SidebarMenuButton>
                    {it.href === "/alerts" && unread > 0 && (
                      <SidebarMenuBadge className="bg-critical text-white">{unread > 99 ? "99+" : unread}</SidebarMenuBadge>
                    )}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  );
}
