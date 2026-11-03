import Link from "next/link";
import { ReactNode } from "react";
import { EU_VISIBLE } from "@/components/layout/nav";
import { Empty } from "@/components/ui";

/** EU Market is hidden from the UI (code + data kept). Direct links land here instead of the pages. */
export default function EuMarketLayout({ children }: { children: ReactNode }) {
  if (EU_VISIBLE) return <>{children}</>;
  return <Empty title="EU Market đang tạm ẩn"><Link href="/" className="underline">Về Daily Pulse</Link></Empty>;
}
