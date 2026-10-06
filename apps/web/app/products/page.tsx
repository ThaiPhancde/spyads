"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Card, Loading, PageHeader, ProductTable } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";

function Explorer() {
  const sp = useSearchParams();
  const meta = useApi("/meta");
  const [f, setF] = useState<any>({
    q: "", country: sp.get("country") || "", category: sp.get("category") || "", recommendation: sp.get("recommendation") || "",
    lifecycle: "", saturation_state: "", min_win: "", max_advertisers: "", min_confidence: "", sort: "opportunity_score", order: "desc",
  });
  const { data, error } = useApi(`/products${qs({ ...f, limit: 300 })}`);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const [merge, setMerge] = useState({ source_id: "", target_id: "" });
  const [msg, setMsg] = useState("");
  useEffect(() => setMsg(""), [merge]);
  const doMerge = async () => {
    try {
      await api("/products/merge", { method: "POST", body: JSON.stringify({ source_id: +merge.source_id, target_id: +merge.target_id }) });
      setMsg("Đã gộp sản phẩm. Tải lại trang để xem.");
    } catch (e: any) { setMsg(`Lỗi: ${e.message}`); }
  };

  const m = meta.data;
  return (
    <div>
      <PageHeader title="Product Explorer" subtitle="Search / filter toàn bộ sản phẩm (Product là entity trung tâm)" />
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-center">
        <input className="input w-56" placeholder="Tên, alias hoặc PRD_…" value={f.q} onChange={set("q")} />
        <select className="input" value={f.country} onChange={set("country")}><option value="">Country</option>{m?.countries.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={f.category} onChange={set("category")}><option value="">Category</option>{m?.categories.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={f.recommendation} onChange={set("recommendation")}><option value="">Action</option>{m?.recommendations.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={f.lifecycle} onChange={set("lifecycle")}><option value="">Lifecycle</option>{m?.lifecycle_states.map((c: string) => <option key={c}>{c}</option>)}</select>
        <select className="input" value={f.saturation_state} onChange={set("saturation_state")}>
          <option value="">Saturation</option>{["Low Saturation", "Growing", "Competitive", "Highly Saturated", "Declining"].map((c) => <option key={c}>{c}</option>)}
        </select>
        <input className="input w-24" placeholder="Win ≥" value={f.min_win} onChange={set("min_win")} />
        <input className="input w-28" placeholder="Advertisers ≤" value={f.max_advertisers} onChange={set("max_advertisers")} />
        <input className="input w-28" placeholder="Confidence ≥" value={f.min_confidence} onChange={set("min_confidence")} />
        <select className="input" value={f.sort} onChange={set("sort")}>
          {[["opportunity_score", "Opportunity"], ["win_score", "Win"], ["rare_winner_score", "Rare winner"], ["rarity_score", "Rarity"], ["saturation_score", "Saturation"], ["confidence_score", "Confidence"], ["growth_7d", "Growth 7d"], ["advertisers", "Advertisers"], ["active_ads", "Active ads"]].map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
        </select>
        <button className="btn" onClick={() => setF({ ...f, order: f.order === "desc" ? "asc" : "desc" })}>{f.order === "desc" ? "↓ Giảm dần" : "↑ Tăng dần"}</button>
      </div>
      {!data ? <Loading error={error} /> : (
        <Card title={`${data.total} sản phẩm`} pad={false}><ProductTable rows={data.rows} /></Card>
      )}
      <Card title="Entity resolution — gộp thủ công 2 sản phẩm trùng" className="mt-4">
        <p className="text-xs text-ink2 mb-2">Khi title/landing khác nhau quá nhiều (vd. “Handheld Car Vacuum” vs “Cordless Handheld Vacuum Cleaner”), gộp sản phẩm nguồn vào sản phẩm đích; toàn bộ ads, comments, orders, experiments được chuyển sang.</p>
        <div className="flex flex-wrap gap-2 items-center">
          <input className="input w-36" placeholder="Source product id" value={merge.source_id} onChange={(e) => setMerge({ ...merge, source_id: e.target.value })} />
          <span className="text-muted">→</span>
          <input className="input w-36" placeholder="Target product id" value={merge.target_id} onChange={(e) => setMerge({ ...merge, target_id: e.target.value })} />
          <button className="btn" onClick={doMerge} disabled={!merge.source_id || !merge.target_id}>Gộp</button>
          {msg && <span className="text-xs text-ink2">{msg}</span>}
        </div>
      </Card>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Explorer /></Suspense>;
}
