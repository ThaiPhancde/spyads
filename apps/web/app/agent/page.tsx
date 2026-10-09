"use client";
import Link from "next/link";
import { useState } from "react";
import { Send } from "lucide-react";
import { Card, PageHeader, RecBadge } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, fmt, useApi } from "@/lib/api";

const EXAMPLES = [
  "Tìm 20 sản phẩm beauty tại Saudi đang tăng nhanh nhưng dưới 10 advertiser.",
  "Cho tôi top complaint của sản phẩm Rosemary Hair Oil",
  "Market nào tuần này có nhiều hidden winner nhất?",
  "Đối thủ nào đang scale nhanh?",
  "Giải thích vì sao PRD_0000001 có điểm này",
];

function cell(k: string, v: any) {
  if (v === null || v === undefined) return "—";
  if (k === "recommendation") return <RecBadge rec={v} />;
  if (["growth_7d", "share", "growth"].includes(k)) return fmt.signedPct(v);
  if (typeof v === "number") return fmt.n(v, Number.isInteger(v) ? 0 : 1);
  return String(v);
}

export default function Agent() {
  const meta = useApi("/meta");
  const [q, setQ] = useState(EXAMPLES[0]);
  const [history, setHistory] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async (question = q) => {
    setBusy(true);
    try { const r = await api("/agent/ask", { method: "POST", body: JSON.stringify({ question }) }); setHistory([r, ...history]); }
    catch (e: any) { setHistory([{ question, answer: `Lỗi: ${e.message}`, rows: [] }, ...history]); }
    finally { setBusy(false); }
  };
  return (
    <div>
      <PageHeader title="AI Agent" subtitle={`Hỏi bằng ngôn ngữ tự nhiên — câu hỏi được dịch thành truy vấn có cấu trúc; số liệu lấy từ database. Engine: ${meta.data?.llm ? `AI (${meta.data.llm_model})` : "rule-based parser (đặt GEMINI_API_KEY để dùng AI)"}`} />
      <Card className="mb-4">
        <div className="flex items-end gap-2">
          <Textarea className="min-h-10 min-w-0 flex-1" rows={2} value={q} onChange={(e) => setQ(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} />
          <Button onClick={() => ask()} disabled={busy}>{busy ? <Spinner /> : <Send />}{busy ? "Đang hỏi…" : "Hỏi"}</Button>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLES.map((e) => <Button key={e} variant="outline" size="xs" className="h-auto max-w-full whitespace-normal py-1 text-left" onClick={() => { setQ(e); ask(e); }}>{e}</Button>)}
        </div>
      </Card>
      <div className="space-y-4">
        {history.map((h, i) => {
          const cols = h.rows?.length ? Object.keys(h.rows[0]).filter((k) => !["id", "product_id", "advertiser_id"].includes(k)) : [];
          return (
            <Card key={i} title={h.question} action={h.spec && <span className="text-[11px] text-muted-foreground">{h.engine} · {h.spec.intent}</span>}>
              <p className="mb-3 text-sm whitespace-pre-wrap">{h.answer}</p>
              {cols.length > 0 && (
                <ScrollArea className="max-h-96 overflow-auto rounded-md border">
                  <Table>
                    <TableHeader className="sticky top-0 z-10 bg-card"><TableRow>{cols.map((c) => <TableHead key={c}>{c.replace(/_/g, " ")}</TableHead>)}</TableRow></TableHeader>
                    <TableBody>{h.rows.map((r: any, j: number) => (
                      <TableRow key={j}>{cols.map((c) => (
                        <TableCell key={c} className="tnum">
                          {c === "name" && r.id ? <Link className="hover:underline" href={`/products/${r.id}`}>{r.name}</Link> :
                            c === "product" && r.product_id ? <Link className="hover:underline" href={`/products/${r.product_id}`}>{r.product}</Link> : cell(c, r[c])}
                        </TableCell>
                      ))}</TableRow>
                    ))}</TableBody>
                  </Table>
                </ScrollArea>
              )}
              {h.spec && (
                <Collapsible className="mt-2">
                  <CollapsibleTrigger className="cursor-pointer text-[11px] text-muted-foreground hover:underline">Query spec</CollapsibleTrigger>
                  <CollapsibleContent><pre className="mt-1 overflow-x-auto rounded-md bg-muted p-2 text-[11px]">{JSON.stringify(h.spec, null, 2)}</pre></CollapsibleContent>
                </Collapsible>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
