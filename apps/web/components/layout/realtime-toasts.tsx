"use client";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { LiveEvent, useEvents } from "@/lib/realtime";

const TOAST_TYPES = new Set(["ALERT", "DECISION_CHANGED", "REFUSAL_SPIKE", "DELIVERY_RATE_DROP", "SEARCH_DONE", "CONNECTOR_FAILED"]);

function text(e: LiveEvent): string {
  const d = e.data || {};
  if (e.type === "DECISION_CHANGED") return `${d.name}: ${d.from} → ${d.to}`;
  if (e.type === "REFUSAL_SPIKE") return `COD refusal tăng ${Math.round(d.from * 100)}% → ${Math.round(d.to * 100)}%`;
  if (e.type === "DELIVERY_RATE_DROP") return `Delivery giảm ${Math.round(d.from * 100)}% → ${Math.round(d.to * 100)}%`;
  if (e.type === "SEARCH_DONE") return d.error && !d.found ? `Tìm kiếm lỗi: ${d.error}` : `Tìm xong: ${d.found} ads, ${d.products} sản phẩm`;
  if (e.type === "CONNECTOR_FAILED") return `${d.connector}: ${d.error}`;
  return d.title || e.type;
}

/** Realtime (SSE) events → sonner toasts. Click opens the product or the alerts page. */
export function RealtimeToasts() {
  const router = useRouter();
  useEvents((e) => {
    if (!TOAST_TYPES.has(e.type)) return;
    const href = e.product_id ? `/products/${e.product_id}` : "/alerts";
    const bad = ["CONNECTOR_FAILED", "REFUSAL_SPIKE", "DELIVERY_RATE_DROP"].includes(e.type) || (e.type === "SEARCH_DONE" && e.data?.error && !e.data?.found);
    (bad ? toast.error : toast)(e.type.replace(/_/g, " "), { description: text(e), action: { label: "Mở", onClick: () => router.push(href) } });
  });
  return null;
}
