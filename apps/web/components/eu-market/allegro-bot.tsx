"use client";
import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { Card } from "@/components/ui";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { ProductGrid } from "@/components/eu-market/shared";
import { api } from "@/lib/api";

const LIMIT = 20, ACK_MS = 1500, POLL_MS = 2000, MAX_MS = 60_000;
type Phase = "" | "noext" | "wait" | "run" | "slow" | "noack";

/** Resolve with the first same-window/same-origin message of `type` (and job_id if given), or null after `ms` / abort. */
function waitMsg(type: string, job_id: string | null, ms: number, signal: AbortSignal): Promise<any> {
  return new Promise((resolve) => {
    const done = (v: any) => { clearTimeout(t); window.removeEventListener("message", on); signal.removeEventListener("abort", stop); resolve(v); };
    const on = (e: MessageEvent) => {
      if (e.source !== window || e.origin !== window.location.origin || e.data?.type !== type) return;
      if (job_id && e.data.job_id !== job_id) return;
      done(e.data);
    };
    const stop = () => done(null);
    const t = setTimeout(stop, ms);
    window.addEventListener("message", on);
    signal.addEventListener("abort", stop);
  });
}

/** Allegro keyword search through the user's Chrome (extensions/allegro-bridge): POST job → extension opens allegro.pl → poll job. */
export function AllegroBotSearch({ keyword, onDone }: { keyword: string; onDone?: () => void }) {
  const [q, setQ] = useState(keyword);
  const [phase, setPhase] = useState<Phase>("");
  const [job, setJob] = useState<any>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const ctl = useRef<AbortController | null>(null);
  useEffect(() => () => ctl.current?.abort(), []);
  useEffect(() => setQ(keyword), [keyword]);

  async function run() {
    const kw = q.trim();
    if (!kw) return setErr("Nhập từ khoá.");
    ctl.current?.abort();
    const c = (ctl.current = new AbortController()), sig = c.signal;
    setErr(""); setJob(null); setPhase("wait"); setBusy(true);
    try {
      const pong = waitMsg("TOOLSPY_ALLEGRO_PONG", null, ACK_MS, sig);
      window.postMessage({ type: "TOOLSPY_ALLEGRO_PING" }, window.location.origin);
      if (!(await pong)) { if (!sig.aborted) setPhase("noext"); return; }
      let j = await api("/eu-market/allegro/search", { method: "POST", body: JSON.stringify({ keyword: kw, limit: LIMIT, provider: "bridge" }) });
      if (sig.aborted) return;
      setJob(j);
      if (!j.final) {
        // always (re)send, also for reused in_flight: the extension dedupes by job_id, and re-runs a job it lost (worker killed)
        const ack = waitMsg("TOOLSPY_ALLEGRO_ACK", j.id, ACK_MS, sig);
        window.postMessage({ type: "TOOLSPY_ALLEGRO_SEARCH", job_id: j.id, keyword: j.keyword, limit: LIMIT }, window.location.origin);
        if (!(await ack)) { if (!sig.aborted) setPhase("noack"); return; }
        setPhase("run");
        const end = Date.now() + MAX_MS;
        while (!j.final && Date.now() < end) {
          const r = await waitMsg("TOOLSPY_ALLEGRO_RESULT", j.id, POLL_MS, sig);   // RESULT → poll right away
          if (sig.aborted) return;
          j = await api(`/eu-market/allegro/jobs/${j.id}`);
          if (sig.aborted) return;
          setJob(j);
          if (r && !j.final) {   // extension finished but its API report did not land → nothing else will update this job
            setErr(`Extension: ${r.error || "đã chạy xong nhưng không gửi được kết quả về API (kiểm tra token / API trong popup)."}`);
            setJob(null); return setPhase("");
          }
        }
        if (!j.final) return setPhase("slow");
      }
      setPhase("");
      onDone?.();
    } catch (e: any) { if (!sig.aborted) { setPhase(""); setErr(String(e.message || e)); } }
    finally { if (!sig.aborted) setBusy(false); }
  }

  const n = job?.rows?.length || job?.items_written || 0;
  const st: [string, boolean] | null =
    phase === "noext" ? ["Chưa cài extension (hoặc chưa tải lại trang sau khi cài) — xem extensions/allegro-bridge/README.md, mục 5.", true]
    : phase === "noack" ? ["Extension không nhận job — tải lại trang ToolSpy rồi thử lại.", true]
    : phase === "wait" ? ["Đang chờ extension…", false]
    : phase === "slow" ? ["Chưa có kết quả sau 60s — job vẫn chạy (tối đa 3 phút), bấm lại để xem tiếp.", false]
    : phase === "run" ? ["Đang lấy dữ liệu từ Allegro…", false]
    : !job ? null
    : job.status === "done" || job.status === "empty" ? [`Xong: ${n} sản phẩm${job.reused === "cache" ? " (từ cache 12h)" : ""}.`, false]
    : job.status === "captcha" ? ["Allegro hiện CAPTCHA — tab Allegro đang mở, tự giải rồi bấm lại.", true]
    : job.status === "blocked" ? [`Bị chặn: ${job.message || "Allegro từ chối truy cập, thử lại sau."}`, true]
    : [`Lỗi: ${job.message || job.error_detail || job.status}`, true];
  return (
    <Card title="Tìm trên Allegro" description="Mở Allegro bằng Chrome của bạn qua extension ToolSpy Allegro Bridge; kết quả lưu vào kho (nguồn Allegro, PL). Cache 12h.">
      <div className="flex flex-wrap items-end gap-2">
        <Input className="w-64" value={q} onChange={(e) => setQ(e.target.value)} placeholder="vd. bransoletka damska" onKeyDown={(e) => e.key === "Enter" && !busy && run()} />
        <Button onClick={run} disabled={busy}>{busy ? <Spinner /> : <Search />}Tìm trên Allegro</Button>
      </div>
      {st && (
        <Alert variant={st[1] ? "destructive" : "default"}>
          <AlertDescription className="flex items-center gap-2">{busy && <Spinner />}{st[0]}</AlertDescription>
        </Alert>
      )}
      {err && <Alert variant="destructive"><AlertDescription>{err}</AlertDescription></Alert>}
      {job?.rows?.length > 0 && <ProductGrid rows={job.rows} />}
    </Card>
  );
}
