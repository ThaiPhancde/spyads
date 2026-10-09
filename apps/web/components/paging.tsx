"use client";
import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

/** Infinite scroll sentinel + explicit button. Loads the next page when scrolled near the bottom. */
export function LoadMore({ hasMore, loading, onMore, shown, total }: { hasMore: boolean; loading: boolean; onMore: () => void; shown: number; total: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const cb = useRef(onMore);
  cb.current = onMore;
  useEffect(() => {
    if (!hasMore || !ref.current) return;
    const io = new IntersectionObserver((e) => { if (e[0].isIntersecting) cb.current(); }, { rootMargin: "600px" });
    io.observe(ref.current);
    return () => io.disconnect();
  }, [hasMore, shown]);
  return (
    <div ref={ref} className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
      {loading ? <><Spinner /> Đang tải thêm…</>
        : hasMore ? <Button variant="outline" size="sm" onClick={onMore}>Tải thêm <span className="tnum text-muted-foreground">({shown}/{total})</span></Button>
        : total > 0 ? `Đã hiển thị hết ${total}` : null}
    </div>
  );
}
