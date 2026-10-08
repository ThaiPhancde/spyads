"use client";
import { useEffect, useRef, useState } from "react";

/**
 * Adaptive (HLS) video player — Video on Demand.
 * - Nothing is fetched until the user presses play (a grid of 30 cards costs 0 bytes of video).
 * - Segments (~4 s) are fetched progressively while watching; only a short forward buffer is kept.
 * - hls.js measures throughput per segment and switches 360p ⇄ 540p: fast network → sharper,
 *   weak network → lighter rendition instead of stalling. Safari uses its native HLS engine.
 */
export function HlsVideo({ src, poster, height, className, fallback }: {
  src: string; poster?: string | null; height: number; className?: string; fallback?: string | null;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<any>(null);
  const [level, setLevel] = useState<string>("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    let cancelled = false;
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = src;  // Safari / iOS: native HLS with its own ABR
      return;
    }
    import("hls.js").then(({ default: Hls }) => {
      if (cancelled || !Hls.isSupported()) { if (!Hls.isSupported() && fallback) video.src = fallback; return; }
      const hls = new Hls({
        autoStartLoad: false,       // wait for play()
        capLevelToPlayerSize: true, // never download more pixels than the card shows
        maxBufferLength: 12,        // keep ~3 segments ahead, not the whole file
        maxMaxBufferLength: 30,
        startLevel: -1,             // first segment: auto, from a quick bandwidth estimate
      });
      hlsRef.current = hls;
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.LEVEL_SWITCHED, (_e: any, d: any) => {
        const l = hls.levels[d.level];
        if (l) setLevel(`${Math.min(l.width, l.height)}p`);
      });
      let netErrors = 0;
      const giveUp = () => { setFailed(true); hls.destroy(); hlsRef.current = null; if (fallback) video.src = fallback; };
      hls.on(Hls.Events.ERROR, (_e: any, d: any) => {
        if (!d.fatal) return;
        if (d.type === Hls.ErrorTypes.NETWORK_ERROR) { if (++netErrors >= 2) giveUp(); else hls.startLoad(); }
        else if (d.type === Hls.ErrorTypes.MEDIA_ERROR) hls.recoverMediaError();
        else giveUp();
      });
      const start = () => hls.startLoad(-1);
      video.addEventListener("play", start, { once: true });
    });
    return () => {
      cancelled = true;
      hlsRef.current?.destroy();
      hlsRef.current = null;
    };
  }, [src, fallback]);

  return (
    <div className="relative">
      <video ref={ref} poster={poster || undefined} controls preload="none" playsInline
             className={className || "w-full rounded-lg bg-black object-contain"} style={{ height }} />
      {level && !failed && (
        <span className="absolute top-2 right-2 text-[10px] rounded px-1.5 py-0.5 pointer-events-none" style={{ background: "rgba(0,0,0,.6)", color: "#fff" }}>
          {level}
        </span>
      )}
    </div>
  );
}
