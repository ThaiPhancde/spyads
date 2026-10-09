"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { BarList } from "@/components/charts";
import { ExperimentCard } from "@/components/ExperimentCard";
import { Card, Empty, Loading, PageHeader, Stat } from "@/components/ui";
import { api, qs, useApi } from "@/lib/api";

const NUM_FIELDS = ["spend", "impressions", "clicks", "landing_views", "atc", "checkout", "purchase", "revenue", "confirmed_orders",
  "shipped", "delivered", "refused", "returned", "ads_submitted", "ads_rejected", "sell_price", "unit_cost"];
const TEXT_FIELDS = ["name", "market", "platform", "creative", "creative_type", "angle", "offer", "funnel", "started_at", "ended_at", "notes"];

/** `product` fixed (opened from a product page) → no catalogue dropdown: the test can only belong to that product. */
function ExperimentForm({ initial, product, onDone, onCancel }: { initial?: any; product?: { id: number; name: string } | null; onDone: () => void; onCancel: () => void }) {
  const pick = !initial?.id && !product;
  const products = useApi(pick ? "/products?limit=500&sort=opportunity_score" : null);
  const [filter, setFilter] = useState("");
  const [v, setV] = useState<any>(initial || { platform: "meta", funnel: "COD", creative_type: "UGC", product_id: product?.id });
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
  const f = filter.trim().toLowerCase();
  const options = (products.data?.rows || []).filter((p: any) => !f || `${p.product_code} ${p.name}`.toLowerCase().includes(f));
  return (
    <Card title={initial?.id ? `Cập nhật: ${initial.name}` : product ? `Tạo test cho: ${product.name}` : "Tạo experiment mới"} className="mb-4">
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-2">
        {pick && (
          <label className="col-span-2 text-xs text-muted">Product
            <input className="input w-full mt-1" placeholder="lọc theo mã / tên…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select className="input w-full mt-1" value={v.product_id || ""} onChange={set("product_id")}>
              <option value="">— chọn ({options.length}) —</option>
              {options.slice(0, 100).map((p: any) => <option key={p.id} value={p.id}>{p.product_code} · {p.name} ({p.recommendation})</option>)}
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
  // ?product_id=…&new=1 from a product page: only that product's tests, form pre-bound (read after mount: no Suspense needed)
  const [pid, setPid] = useState<number | null>(null);
  const [form, setForm] = useState<any>(null);
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const id = Number(sp.get("product_id"));
    if (id) { setPid(id); if (sp.get("new")) setForm({}); }
  }, []);
  const { data, error, reload } = useApi(`/experiments${qs({ status, failure_type: failure, product_id: pid })}`);
  const product = useApi(pid ? `/products/${pid}` : null);
  const bound = pid ? { id: pid, name: product.data?.product?.name || `#${pid}` } : null;
  if (!data) return <Loading error={error} />;
  const s = data.summary;
  return (
    <div>
      <PageHeader title="Test Lab" subtitle={bound ? `Chỉ hiện test của: ${bound.name}` : "Experiment tracking + phân tích vì sao test thất bại (creative, landing, price, offer, logistics…)"}>
        {bound && <Link href={`/products/${pid}`} className="btn text-xs">← Sản phẩm</Link>}
        {bound && <button className="btn text-xs" onClick={() => { setPid(null); window.history.replaceState(null, "", "/test-lab"); }}>Xem tất cả test</button>}
        <select className="input" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">Mọi trạng thái</option>{["WIN", "PROMISING", "FAILED", "WAITING"].map((x) => <option key={x}>{x}</option>)}
        </select>
        <select className="input" value={failure} onChange={(e) => setFailure(e.target.value)}>
          <option value="">Mọi failure type</option>{Object.keys(data.failures).map((x) => <option key={x}>{x}</option>)}
        </select>
        <button className="btn btn-primary" onClick={() => setForm({})}>+ Experiment{bound ? " cho sản phẩm này" : ""}</button>
      </PageHeader>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <Stat label="Testing (running)" value={data.running} />
        <Stat label="Winning" value={s.WIN || 0} />
        <Stat label="Promising" value={s.PROMISING || 0} />
        <Stat label="Failed" value={s.FAILED || 0} />
        <Stat label="Waiting data" value={s.WAITING || 0} />
      </div>
      {form && <ExperimentForm initial={form.id ? form : undefined} product={form.id ? null : bound} onCancel={() => setForm(null)} onDone={() => { setForm(null); reload(); }} />}
      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4">
        <Card title="Nguyên nhân thất bại" className="xl:col-span-1 self-start">
          <BarList rows={Object.entries(data.failures).map(([k, v]) => ({ label: k, value: v as number }))} color="var(--serious)" />
        </Card>
        <div className="xl:col-span-3 space-y-4">
          {data.rows.map((e: any) => (
            <ExperimentCard key={e.id} e={e} onEdit={() => setForm({ ...e, started_at: e.started_at, ended_at: e.ended_at || "" })} />
          ))}
          {!data.rows.length && <Card><Empty>{bound ? "Sản phẩm này chưa có test nào — bấm “+ Experiment cho sản phẩm này”." : "Chưa có experiment."}</Empty></Card>}
        </div>
      </div>
    </div>
  );
}
