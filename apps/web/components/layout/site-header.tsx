"use client";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import { titleFor } from "@/components/layout/nav";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useLiveStatus } from "@/lib/realtime";
import { Badge } from "@/components/ui/badge";

export function SiteHeader() {
  const path = usePathname();
  const live = useLiveStatus();
  return (
    <header className="flex h-(--header-height) shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear">
      <div className="flex w-full items-center gap-1 px-4 lg:gap-2 lg:px-6">
        <SidebarTrigger className="-ml-1" />
        <Separator orientation="vertical" className="mx-2 data-[orientation=vertical]:h-4" />
        <h1 className="text-base font-medium">{titleFor(path)}</h1>
        <div className="ml-auto flex items-center gap-2">
          <Badge variant="outline" className="hidden gap-1.5 sm:inline-flex">
            <span className={`size-1.5 rounded-full ${live === "live" ? "bg-good" : live === "offline" ? "bg-critical" : "bg-warning"}`} />
            {live === "live" ? "Realtime" : live === "offline" ? "Offline" : "Đang kết nối"}
          </Badge>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
