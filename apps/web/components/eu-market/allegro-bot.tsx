"use client";
import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import { SimpleSelect } from "@/components/simple-select";
import { Card } from "@/components/ui";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { ProductGrid } from "@/components/eu-market/shared";
import { api } from "@/lib/api";

const PROVIDERS: [string, string][] = [["serp", "SerpApi (Google Shopping)"], ["grok", "Grok Bot"]];

/** Allegro keyword search via the backend bot (POST /eu-market/allegro/search), polled until the job is final. */
export function AllegroBotSearch({ keyword, onDone }: { keyword: string; onDone?: () => void }) {
  const [q, setQ] = useState(keyword);
  const [provider, setProvider] = useState("serp");
  const [force, setForce] = useState(false);
  const [job, setJob] = useState<any>(null);
  const [err, setErr] = useState("");
  const alive = useRef(true);
  useEffect(() => () => { alive.current = false; }, []);
  useEffect(() => setQ(keyword), [keyword]);

  async function run() {
    if (!q.trim()) return setErr("Nhập từ khoá.");
    setErr(""); setJob(null);
    try {
      let j = await api("/eu-market/allegro/search", { method: "POST", body: JSON.stringify({ keyword: q.trim(), provider, force }) });
      setJob(j);
      while (!j.final && alive.current) {
        await new Promise((r) => setTimeout(r, 3000));
        j = await api(`/eu-market/allegro/jobs/${j.id}`);
        if (alive.current) setJob(j);
      }
      onDone?.();
    } catch (e: any) { setErr(String(e.message || e)); }
  }

  const busy = job && !job.final;
  return (
    <Card title="Tìm trên Allegro" description="Bot tìm theo từ khoá, kết quả lưu vào kho (nguồn Allegro, PL). Cache 12h — tick “Tìm lại” để bỏ cache (tốn quota).">
      <div className="flex flex-wrap items-end gap-2">
        <Input className="w-64" value={q} onChange={(e) => setQ(e.target.value)} placeholder="vd. bransoletka damska" onKeyDown={(e) => e.key === "Enter" && !busy && run()} />
        <SimpleSelect value={provider} onChange={setProvider} options={PROVIDERS} />
        <Label className="flex items-center gap-2 text-sm"><Checkbox checked={force} onCheckedChange={(v) => setForce(!!v)} />Tìm lại</Label>
        <Button onClick={run} disabled={!!busy}>{busy ? <Spinner /> : <Search />}Tìm</Button>
      </div>
      {err && <Alert variant="destructive"><AlertDescription>{err}</AlertDescription></Alert>}
      {job && (
        <Alert variant={["failed", "timeout", "captcha", "blocked"].includes(job.status) ? "destructive" : "default"}>
          <AlertDescription className="flex items-center gap-2">
            {busy && <Spinner />}
            {job.status}{job.reused ? ` (${job.reused === "cache" ? "từ cache" : "đang chạy sẵn"})` : ""} · {job.items_written ?? 0} listing
            {job.message || job.error_detail ? ` · ${job.message || job.error_detail}` : ""}
          </AlertDescription>
        </Alert>
      )}
      {job?.rows?.length > 0 && <ProductGrid rows={job.rows} />}
    </Card>
  );
}
