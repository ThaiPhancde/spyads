"use client";
import { useEffect, useRef } from "react";

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
    <div ref={ref} className="py-6 text-center text-sm text-ink2">
      {loading ? "Đang tải thêm…" : hasMore ? <button className="btn" onClick={onMore}>Tải thêm ({shown}/{total})</button> : total > 0 ? `Đã hiển thị hết ${total}` : null}
    </div>
  );
}
