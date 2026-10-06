"use client";
import { useState } from "react";
import { BarList } from "@/components/charts";
import { ExperimentCard } from "@/components/ExperimentCard";
import { Card, Loading, PageHeader, Stat } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";

const NUM_FIELDS = ["spend", "impressions", "clicks", "landing_views", "atc", "checkout", "purchase", "revenue", "confirmed_orders",
  "shipped", "delivered", "refused", "returned", "ads_submitted", "ads_rejected", "sell_price", "unit_cost"];
const TEXT_FIELDS = ["name", "market", "platform", "creative", "creative_type", "angle", "offer", "funnel", "started_at", "ended_at", "notes"];

function ExperimentForm({ initial, onDone, onCancel }: { initial?: any; onDone: () => void; onCancel: () => void }) {
  const products = useApi("/products?limit=500&sort=opportunity_score");
  const [v, setV] = useState<any>(initial || { platform: "meta", funnel: "COD", creative_type: "UGC" });
  const [err, setErr] = useState("");
  const set = (k: string) => (e: any) => setV({ ...v, [k]: e.target.value });
  const save = async () => {
    const body: any = {};
    for (const k of NUM_FIELDS) if (v[k] !== undefined && v[k] !== "" && v[k] !== null) body[k] = Number(v[k]);
    for (const k of TEXT_FIELDS) if (v[k] !== undefined && v[k] !== null) body[k] = v[k] === "" ? null : v[k];
    try {
      if (initial?.id) await api(`/experiments/${initial.id}`, { method: "PATCH", body: JSON.stringify(body) });
      else await api("/experiments", { method: "POST", body: JSON.stringify({ ...body, product_id: Number(v.product_id) }) });
      onDone();
    } catch (e: any) { setErr(e.message); }
  };
  return (
    <Card title={initial?.id ? `Cập nhật: ${initial.name}` : "Tạo experiment mới"} className="mb-4">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
        {!initial?.id && (
          <label className="col-span-2 text-xs text-muted">Product
            <select className="input w-full mt-1" value={v.product_id || ""} onChange={set("product_id")}>
              <option value="">— chọn —</option>
              {products.data?.rows.map((p: any) => <option key={p.id} value={p.id}>{p.product_code} · {p.name} ({p.recommendation})</option>)}
            </select>
          </label>
        )}
        {TEXT_FIELDS.filter((k) => k !== "notes").map((k) => (
          <label key={k} className="text-xs text-muted">{k}
            <input className="input w-full mt-1" type={k.endsWith("_at") ? "date" : "text"} value={v[k] ?? ""} onChange={set(k)} />
          </label>
        ))}
        {NUM_FIELDS.map((k) => (
          <label key={k} className="text-xs text-muted">{k}
            <input className="input w-full mt-1 tnum" inputMode="decimal" value={v[k] ?? ""} onChange={set(k)} />
          </label>
        ))}
      </div>
      <div className="flex gap-2 mt-3 items-center">
        <button className="btn btn-primary" onClick={save} disabled={!initial?.id && !v.product_id}>Lưu & phân tích</button>
        <button className="btn" onClick={onCancel}>Huỷ</button>
        {err && <span className="text-xs" style={{ color: "var(--critical)" }}>{err}</span>}
        <span className="text-xs text-muted">Điền ended_at khi test kết thúc → lifecycle tự chuyển WIN / HOLD / FAIL.</span>
      </div>
    </Card>
  );
}

export default function TestLab() {
  const [status, setStatus] = useState("");
  const [failure, setFailure] = useState("");
  const { data, error, reload } = useApi(`/experiments${qs({ status, failure_type: failure })}`);
  const [form, setForm] = useState<any>(null);
  if (!data) return <Loading error={error} />;
  const s = data.summary;
  return (
    <div>
      <PageHeader title="Test Lab" subtitle="Experiment tracking + phân tích vì sao test thất bại (creative, landing, price, offer, logistics…)">
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Mọi trạng thái</option>{["WIN", "PROMISING", "FAILED", "WAITING"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select className="input" value={failure} onChange={(e) => setFailure(e.target.value)}>
          <option value="">Mọi failure type</option>{Object.keys(data.failures).map((x) => <option key={x}>{x}</option>)}
        </select>
        <button className="btn btn-primary" onClick={() => setForm({})}>+ Experiment</button>
      </PageHeader>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <Stat label="Testing (running)" value={data.running} />
        <Stat label="Winning" value={s.WIN || 0} />
        <Stat label="Promising" value={s.PROMISING || 0} />
        <Stat label="Failed" value={s.FAILED || 0} />
        <Stat label="Waiting data" value={s.WAITING || 0} />
      </div>
      {form && <ExperimentForm initial={form.id ? form : undefined} onCancel={() => setForm(null)} onDone={() => { setForm(null); reload(); }} />}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        <Card title="Nguyên nhân thất bại" className="xl:col-span-1 self-start">
          <BarList rows={Object.entries(data.failures).map(([k, v]) => ({ label: k, value: v as number }))} color="var(--serious)" />
        </Card>
        <div className="xl:col-span-3 space-y-4">
          {data.rows.map((e: any) => (
            <ExperimentCard key={e.id} e={e} onEdit={() => setForm({ ...e, started_at: e.started_at, ended_at: e.ended_at || "" })} />
          ))}
        </div>
      </div>
    </div>
  );
}
