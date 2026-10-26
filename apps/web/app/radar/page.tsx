"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { CHANNEL_TABS, DISCOVERY_TABS, DiscoveryView } from "@/components/discover/discovery";
import { LiveView } from "@/components/discover/live";
import { LowCompetitionView } from "@/components/discover/low-competition";
import { MarketsView } from "@/components/discover/markets";
import { TableView } from "@/components/discover/table";
import { Loading } from "@/components/ui";
import { cn } from "@/lib/utils";

/** One "Khám phá sản phẩm" page (design §3b): the five old product-list pages are views; the view lives in the URL so
 *  old links (/product-radar, /hidden-winners, /products, /markets) redirect here and stay shareable. */
const VIEWS: [string, string][] = [
  ["channel", "Theo kênh"], ["discovery", "Discovery"], ["live", "Đang chạy"], ["low-competition", "Ít cạnh tranh"],
  ["table", "Bảng xếp hạng"], ["markets", "Thị trường"],
];

function View({ view }: { view: string }) {
  switch (view) {
    case "discovery":
      return <DiscoveryView key="discovery" tabs={DISCOVERY_TABS} chart title="Discovery"
                            subtitle="Không chỉ “sản phẩm bán chạy” — sản phẩm độc lạ, có sóng, có chất liệu creative, hợp market và hợp gu team MKT" />;
    case "live": return <LiveView />;
    case "low-competition": return <LowCompetitionView />;
    case "table": return <TableView />;
    case "markets": return <MarketsView />;
    default:
      return <DiscoveryView key="channel" tabs={CHANNEL_TABS} title="Theo kênh bán"
                            subtitle="Video Win / Video Scale cho người chạy Facebook · Tiềm năng cho TikTok · Bò sữa cho Google / SEO / sàn / free traffic. Chỉ tính ad được nguồn xác nhận còn chạy." />;
  }
}

function Discover() {
  const sp = useSearchParams();
  const view = VIEWS.some(([k]) => k === sp.get("view")) ? sp.get("view")! : "channel";
  return (
    <div className="space-y-4">
      <nav aria-label="Chế độ xem" className="flex flex-wrap gap-1.5 border-b pb-2">
        {VIEWS.map(([k, label]) => (
          <Link key={k} href={`/radar?view=${k}`} aria-current={k === view ? "page" : undefined}
                className={cn("rounded-md px-3 py-1.5 text-sm transition-colors",
                              k === view ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
            {label}
          </Link>
        ))}
      </nav>
      <View view={view} />
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Discover /></Suspense>;
}
