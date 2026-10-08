"use client";
import { useEffect, useRef, useState } from "react";

export type LiveEvent = { type: string; product_id: number | null; data: any; at: string };

type Handler = (e: LiveEvent) => void;
const handlers = new Set<Handler>();
let source: EventSource | null = null;
let status: "connecting" | "live" | "offline" = "connecting";
const statusListeners = new Set<(s: typeof status) => void>();

function ensure() {
  if (source || typeof window === "undefined") return;
  const base = process.env.NEXT_PUBLIC_EVENTS_URL || "/api/events/stream";
  source = new EventSource(base);
  const setStatus = (s: typeof status) => { status = s; statusListeners.forEach((l) => l(s)); };
  source.onopen = () => setStatus("live");
  source.onerror = () => setStatus("offline");
  source.onmessage = (m) => dispatch(m.data);
  [
    "ORDER_CREATED", "ORDER_CONFIRMED", "ORDER_CANCELLED", "SHIPMENT_CREATED", "SHIPMENT_DELIVERED", "SHIPMENT_FAILED",
    "SHIPMENT_REFUSED", "SHIPMENT_RETURNED", "COMMENT_CREATED", "PRODUCT_DISCOVERED", "DECISION_CHANGED", "CREATIVE_STORED",
    "REFUSAL_SPIKE", "DELIVERY_RATE_DROP", "CONNECTOR_SYNCED", "CONNECTOR_FAILED", "SEARCH_PROGRESS", "SEARCH_DONE", "ALERT",
    "RARE_WINNER_DETECTED", "PRODUCT_GROWTH_SPIKE", "AD_CREATED", "AD_REJECTED", "ROAS_DROP", "NEGATIVE_SENTIMENT_SPIKE",
    "LIVENESS_CHECKED", "AD_STOPPED", "AD_REACTIVATED", "STORAGE_ROLLOVER",
  ].forEach((t) => source!.addEventListener(t, (m: MessageEvent) => dispatch(m.data)));
}

function dispatch(raw: string) {
  try {
    const e = JSON.parse(raw) as LiveEvent;
    handlers.forEach((h) => h(e));
  } catch {}
}

/** Subscribe to realtime events from the API (Server-Sent Events). */
export function useEvents(handler: Handler, deps: any[] = []) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    ensure();
    const h: Handler = (e) => ref.current(e);
    handlers.add(h);
    return () => { handlers.delete(h); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function useLiveStatus() {
  const [s, setS] = useState(status);
  useEffect(() => {
    ensure();
    statusListeners.add(setS);
    return () => { statusListeners.delete(setS); };
  }, []);
  return s;
}

/** Who is voting / searching — stored per browser. */
export function getUser(): { name: string; team: string } {
  if (typeof window === "undefined") return { name: "", team: "" };
  try {
    return { name: localStorage.getItem("mi_user") || "", team: localStorage.getItem("mi_team") || "" };
  } catch {
    return { name: "", team: "" };
  }
}

export function setUser(name: string, team: string) {
  try {
    localStorage.setItem("mi_user", name);
    localStorage.setItem("mi_team", team);
  } catch {}
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("mi-team", { detail: team }));
}

/** Global team filter (MKT Mess = inbox/WhatsApp ads, MKT Ladi = landing-page ads, "" = all).
 *  Every discovery page filters by it; changes in the sidebar apply instantly. */
export function useTeam(): string {
  const [team, setTeam] = useState("");
  useEffect(() => {
    setTeam(getUser().team);
    const h = (e: Event) => setTeam((e as CustomEvent).detail || "");
    window.addEventListener("mi-team", h);
    return () => window.removeEventListener("mi-team", h);
  }, []);
  return team;
}
