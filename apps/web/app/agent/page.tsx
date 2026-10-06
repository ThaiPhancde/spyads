"use client";
import Link from "next/link";
import { useState } from "react";
import { Card, PageHeader, RecBadge } from "@/components/ui";
import { api, fmt, useApi } from "@/lib/api";

const EXAMPLES = [
  "Tìm 20 sản phẩm beauty tại Saudi đang tăng nhanh nhưng dưới 10 advertiser.",
  "Sản phẩm nào trong 30 ngày qua có Win Score cao nhưng COD refusal >20%?",
  "Những sản phẩm chúng ta đã test thất bại do creative?",
  "Cho tôi top complaint của sản phẩm Rosemary Hair Oil",
  "Market nào tuần này có nhiều hidden winner nhất?",
  "Đối thủ nào đang scale nhanh?",
  "Giải thích vì sao PRD_0000001 có điểm này",
];

function cell(k: string, v: any) {
  if (v === null || v === undefined) return "—";
  if (k === "recommendation") return <RecBadge rec={v} />;
  if (["growth_7d", "share", "refusal_rate", "growth"].includes(k)) return fmt.signedPct(v);
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
      <PageHeader title="AI Agent" subtitle={`Hỏi bằng ngôn ngữ tự nhiên — câu hỏi được dịch thành truy vấn có cấu trúc; số liệu lấy từ database. Engine: ${meta.data?.llm ? `Claude (${meta.data.llm_model})` : "rule-based parser (đặt ANTHROPIC_API_KEY để dùng Claude)"}`} />
      <Card className="mb-4">
        <div className="flex gap-2 pt-4">
          <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask()} />
          <button className="btn btn-primary" onClick={() => ask()} disabled={busy}>{busy ? "Đang hỏi…" : "Hỏi"}</button>
        </div>
        <div className="flex flex-wrap gap-1.5 mt-3">
          {EXAMPLES.map((e) => <button key={e} className="btn text-xs" onClick={() => { setQ(e); ask(e); }}>{e}</button>)}
        </div>
      </Card>
      <div className="space-y-4">
        {history.map((h, i) => {
          const cols = h.rows?.length ? Object.keys(h.rows[0]).filter((k) => !["id", "product_id", "advertiser_id", "experiment_id"].includes(k)) : [];
          return (
            <Card key={i} title={h.question} action={h.spec && <span className="text-[11px] text-muted">{h.engine} · {h.spec.intent}</span>}>
              <p className="text-sm mb-3 whitespace-pre-wrap">{h.answer}</p>
              {cols.length > 0 && (
                <div className="overflow-x-auto max-h-96">
                  <table className="data"><thead><tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, " ")}</th>)}</tr></thead>
                    <tbody>{h.rows.map((r: any, j: number) => (
                      <tr key={j}>{cols.map((c) => (
                        <td key={c} className="tnum">
                          {c === "name" && r.id ? <Link className="hover:underline" href={`/products/${r.id}`}>{r.name}</Link> :
                            c === "product" && r.product_id ? <Link className="hover:underline" href={`/products/${r.product_id}`}>{r.product}</Link> : cell(c, r[c])}
                        </td>
                      ))}</tr>
                    ))}</tbody></table>
                </div>
              )}
              {h.spec && <details className="mt-2"><summary className="text-[11px] text-muted cursor-pointer">Query spec</summary><pre className="text-[11px] mt-1">{JSON.stringify(h.spec, null, 2)}</pre></details>}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
