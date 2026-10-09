"use client";
import { useEffect, useState } from "react";
import { ChevronsUpDown, UserRound } from "lucide-react";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { getUser, setUser, useLiveStatus } from "@/lib/realtime";

const TEAMS: [string, string, string][] = [
  ["", "Tất cả", "Không lọc theo funnel"],
  ["mess", "MKT Mess", "Chỉ quảng cáo chốt qua inbox / WhatsApp / Messenger"],
  ["ladi", "MKT Ladi", "Chỉ quảng cáo dẫn về landing page / website"],
];

const LIVE: Record<string, [string, string]> = {
  live: ["bg-good", "Realtime đang kết nối"], offline: ["bg-critical", "Mất kết nối realtime"], connecting: ["bg-warning", "Đang kết nối…"],
};

/** Who is voting / searching + the global MKT team filter (Mess / Ladi), stored per browser. */
export function NavUser() {
  const { isMobile } = useSidebar();
  const live = useLiveStatus();
  const [name, setName] = useState("");
  const [team, setTeam] = useState("");
  useEffect(() => { const u = getUser(); setName(u.name); setTeam(u.team); }, []);
  const teamLabel = TEAMS.find(([k]) => k === team)?.[1] || "Tất cả";
  const [dot, liveLabel] = LIVE[live] || LIVE.connecting;

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <Popover>
          <PopoverTrigger asChild>
            <SidebarMenuButton size="lg" tooltip={`${name || "Chưa đặt tên"} · ${teamLabel}`}
                               className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground">
              <Avatar className="size-8 rounded-lg">
                <AvatarFallback className="rounded-lg text-xs font-semibold">{name ? name.slice(0, 2).toUpperCase() : <UserRound className="size-4" />}</AvatarFallback>
              </Avatar>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{name || "Nhập tên của bạn"}</span>
                <span className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                  <span className={`inline-block size-1.5 rounded-full ${dot}`} />{teamLabel}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto size-4" />
            </SidebarMenuButton>
          </PopoverTrigger>
          <PopoverContent side={isMobile ? "top" : "right"} align="end" sideOffset={8} className="w-72 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="mi-user">Tên bạn (để vote / lưu)</Label>
              <Input id="mi-user" value={name} placeholder="vd. nhat" onChange={(e) => { setName(e.target.value); setUser(e.target.value, team); }} />
            </div>
            <div className="space-y-1.5">
              <Label>Team MKT — lọc toàn app</Label>
              <ToggleGroup type="single" variant="outline" size="sm" value={team} className="w-full"
                           onValueChange={(v) => { setTeam(v ?? ""); setUser(name, v ?? ""); }}>
                {TEAMS.map(([k, l, tip]) => <ToggleGroupItem key={k || "all"} value={k} title={tip} className="flex-1">{l}</ToggleGroupItem>)}
              </ToggleGroup>
              <p className="text-xs leading-snug text-muted-foreground">{TEAMS.find(([k]) => k === team)?.[2]}</p>
            </div>
            <div className="flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5"><span className={`inline-block size-2 rounded-full ${dot}`} />{liveLabel}</span>
              <ThemeToggle />
            </div>
          </PopoverContent>
        </Popover>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
