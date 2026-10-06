"use client";
import Link from "next/link";
import { useState } from "react";
import { CommentsPanel } from "@/components/CommentsPanel";
import { Card, Loading, PageHeader, Pill } from "@/components/ui";
import { api, fmt, qs, useApi } from "@/lib/api";

export default function CommentIntel() {
  const meta = useApi("/meta");
  const [country, setCountry] = useState("");
  const [category, setCategory] = useState("");
  const [days, setDays] = useState("");
  const { data, error } = useApi(`/comments/intelligence${qs({ country, category, days })}`);
  const [text, setText] = useState("Sản phẩm dùng ổn nhưng giao quá chậm và giá hơi cao.");
  const [res, setRes] = useState<any>(null);
  const analyze = async () => setRes(await api("/comments/analyze", { method: "POST", body: JSON.stringify({ texts: text.split("\n").filter(Boolean) }) }));

  return (
    <div>
      <PageHeader title="Comment Intelligence" subtitle="Aspect-Based Sentiment Analysis trên comment / review / inbox / call notes">
        <select className="input" value={country} onChange={(e) => setCountry(e.target.value)}><option value="">Country</option>{meta.data?.countries.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">Category</option>{meta.data?.categories.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={days} onChange={(e) => setDays(e.target.value)}><option value="">Toàn bộ</option><option value="7">7 ngày</option><option value="30">30 ngày</option></select>
      </PageHeader>
      {!data ? <Loading error={error} /> : (
        <>
          <CommentsPanel c={data} />
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mt-4">
            <Card title="Sản phẩm nhiều comment tiêu cực nhất" pad={false}>
              <table className="data">
                <thead><tr><th>Product</th><th>Comments</th><th>Negative</th><th>Neg. 7d vs trước</th><th>Complaint rate</th></tr></thead>
                <tbody>{data.products.map((p: any) => {
                  const up = p.negative_rate_7d !== null && p.negative_rate_prev !== null && p.negative_rate_7d - p.negative_rate_prev >= 0.1;
                  return (
                    <tr key={p.id}>
                      <td><Link className="hover:underline" href={`/products/${p.id}`}>{p.name}</Link></td>
                      <td className="tnum">{p.comments}</td><td className="tnum">{fmt.pct(p.negative_rate, 0)}</td>
                      <td className="tnum">{fmt.pct(p.negative_rate_prev, 0)} → <b style={{ color: up ? "var(--critical)" : undefined }}>{fmt.pct(p.negative_rate_7d, 0)}</b> {up && "▲"}</td>
                      <td className="tnum">{fmt.pct(p.complaint_rate, 0)}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </Card>
            <Card title="Thử phân tích comment (mỗi dòng 1 comment)">
              <textarea className="input w-full h-24" value={text} onChange={(e) => setText(e.target.value)} />
              <button className="btn btn-primary mt-2" onClick={analyze}>Phân tích</button>
              {res && (
                <div className="mt-3 space-y-2">
                  <div className="text-[11px] text-muted">Engine: {res.engine}</div>
                  {res.results.map((r: any, i: number) => (
                    <div key={i} className="text-xs rounded-lg p-2" style={{ background: "var(--surface-2)" }}>
                      <div className="mb-1">“{r.text}”</div>
                      <pre className="text-[11px] whitespace-pre-wrap">{JSON.stringify({ overall: r.overall, ...r.aspects, purchase_intent: r.purchase_intent }, null, 2)}</pre>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
          <Card title="Comment mới nhất" className="mt-4" pad={false}>
            <div className="max-h-96 overflow-y-auto">
              <table className="data"><thead><tr><th>Thời gian</th><th>Comment</th><th>Overall</th><th>Aspects</th><th>Intent</th><th>Nguồn</th></tr></thead>
                <tbody>{data.latest.map((c: any) => (
                  <tr key={c.id}><td className="whitespace-nowrap text-xs">{fmt.dt(c.created_at)}</td><td className="max-w-md">{c.text}</td>
                    <td><Pill tone={c.overall === "positive" ? "info" : c.overall === "negative" ? "bad" : "neutral"}>{c.overall}</Pill></td>
                    <td className="text-xs">{Object.entries(c.aspects || {}).map(([a, v]) => `${a}: ${v === "positive" ? "+" : "−"}`).join(", ")}</td>
                    <td className="text-xs">{c.purchase_intent}</td><td className="text-xs">{c.source}</td></tr>
                ))}</tbody></table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
