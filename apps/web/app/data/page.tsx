"use client";
import { useState } from "react";
import { Card, Loading, PageHeader, Pill } from "@/components/ui";
import { api, fmt, useAction, useApi } from "@/lib/api";
import { getUser, useEvents } from "@/lib/realtime";
import { NetworkBadge, usePlatforms } from "@/components/platforms";

const CHANNEL_ORDER: [string, string][] = [["ads", "Thư viện quảng cáo"], ["commerce", "Nguồn hàng & store"]];
const INTERNAL = new Set(["internal_ads", "business", "feedback"]);
const HEALTH: Record<string, ["good" | "warn" | "bad" | "neutral" | "info", string]> = {
  healthy: ["good", "🟢 Healthy"], slow: ["warn", "🟡 Slow"], auth_expired: ["bad", "🔴 Auth expired"], error: ["bad", "🔴 Error"],
  degraded: ["warn", "🟡 Degraded"], unknown: ["neutral", "⚪ Chưa chạy"],
};

function ConnectorRow({ c, onChange, byKey }: { c: any; onChange: () => void; byKey: any }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<Record<string, any>>(c.config || {});
  const [every, setEvery] = useState(c.every_minutes ?? "");
  const [msg, setMsg] = useState("");
  const act = useAction();
  const save = () => act.run(async () => {
    await api(`/collector/connectors/${c.id}`, { method: "PATCH", body: JSON.stringify({ config: cfg, every_minutes: every === "" ? 0 : Number(every) }) });
    setMsg("Đã lưu"); onChange();
  });
  const toggle = () => act.run(async () => { await api(`/collector/connectors/${c.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !c.enabled }) }); onChange(); });
  const test = () => act.run(async () => { setMsg("Đang kiểm tra…"); const r = await api(`/collector/connectors/${c.id}/test`, { method: "POST" }); setMsg(JSON.stringify(r)); onChange(); });
  const sync = () => act.run(async () => { await api(`/collector/connectors/${c.id}/sync`, { method: "POST" }); setMsg("Đã đưa vào hàng đợi — kết quả hiện ở feed realtime"); });
  const [tone, label] = HEALTH[c.health] || HEALTH.unknown;
  return (
    <div className="px-4 py-3 border-b" style={{ borderColor: "var(--grid)" }}>
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex-1 min-w-[240px]">
          <div className="text-sm font-medium flex flex-wrap items-center gap-1.5"><NetworkBadge network={c.network} byKey={byKey} small />{c.name} <span className="text-[11px] text-muted">· {c.adapter} · {c.kind}</span></div>
          <div className="text-[11px] text-muted">
            Lần chạy: {fmt.dt(c.last_sync_at)} · {c.last_sync_count} bản ghi{c.last_duration_ms ? ` · ${(c.last_duration_ms / 1000).toFixed(1)}s` : ""}
            {c.every_minutes ? ` · tự chạy mỗi ${c.every_minutes} phút` : " · chạy khi tìm kiếm / theo dõi từ khoá"}
          </div>
          {c.last_error && <div className="text-[11px]" style={{ color: "var(--critical)" }}>{c.last_error}</div>}
        </div>
        <Pill tone={tone}>{label}</Pill>
        <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={c.enabled} onChange={toggle} /> Bật</label>
        <button className="btn text-xs" onClick={() => setOpen(!open)}>Cấu hình</button>
        <button className="btn text-xs" onClick={test}>Test</button>
        <button className="btn text-xs" onClick={sync}>Sync ngay</button>
      </div>
      {act.error ? <div className="text-[11px] mt-1 break-all" style={{ color: "var(--critical)" }}>Lỗi: {act.error}</div>
        : msg && <div className="text-[11px] text-ink2 mt-1 break-all">{msg}</div>}
      {open && (
        <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-2">
          {(c.config_fields || []).map((f: any) => (
            <label key={f.key} className="text-xs">
              <div className="text-muted mb-0.5">{f.label}{f.required ? " *" : ""}</div>
              {/keywords|mapping|input|body|headers|product_map/.test(f.key) ? (
                <textarea className="input w-full h-20 font-mono text-[11px]" value={cfg[f.key] ?? ""} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
              ) : (
                <input className="input w-full" type={f.secret ? "password" : "text"} value={cfg[f.key] ?? ""} onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
              )}
            </label>
          ))}
          <label className="text-xs">
            <div className="text-muted mb-0.5">Tự chạy mỗi N phút (để trống = chỉ chạy khi tìm kiếm / có tracked query)</div>
            <input className="input w-full" value={every} onChange={(e) => setEvery(e.target.value.replace(/\D/g, ""))} />
          </label>
          <div className="md:col-span-2"><button className="btn btn-primary text-xs" onClick={save}>Lưu cấu hình</button></div>
        </div>
      )}
    </div>
  );
}

/** Is every platform flowing? One row per network: records held, new in 24 h, connectors on / healthy, last error. */
function SourcesCard({ sources, byKey }: { sources: any[]; byKey: any }) {
  if (!sources.length) return null;
  return (
    <Card title="Nguồn theo nền tảng" pad={false} className="mb-4">
      <div className="overflow-x-auto">
        <table className="data">
          <thead><tr><th>Nền tảng</th><th>Kênh</th><th className="text-right">Bản ghi</th><th className="text-right">Mới 24h</th><th>Connector</th><th>Lần chạy</th><th>Ghi chú</th></tr></thead>
          <tbody>
            {CHANNEL_ORDER.flatMap(([ch]) => sources.filter((s) => s.channel === ch)).map((s) => {
              const tone = s.enabled === 0 ? "var(--muted)" : s.healthy === s.enabled ? "var(--good)" : s.healthy ? "var(--warning)" : "var(--critical)";
              return (
                <tr key={s.network}>
                  <td><NetworkBadge network={s.network} byKey={byKey} /></td>
                  <td className="text-xs text-ink2">{s.channel_label}</td>
                  <td className="text-right tnum">{fmt.n(s.records)}</td>
                  <td className="text-right tnum">{s.new_24h ? `+${fmt.n(s.new_24h)}` : "—"}</td>
                  <td className="text-xs whitespace-nowrap"><span className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: tone }} />
                    {s.connectors ? `${s.enabled}/${s.connectors} bật · ${s.healthy} ổn` : "chưa có connector"}</td>
                  <td className="text-xs whitespace-nowrap">{fmt.dt(s.last_sync_at)}</td>
                  <td className="text-[11px] max-w-[360px]" style={{ color: s.errors.length ? "var(--critical)" : undefined }}>
                    {s.errors.length ? s.errors.join(" · ") : s.enabled ? "" : "Tắt — bật ở danh sách connector bên dưới"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

const mb = (b: number) => (b >= 1073741824 ? `${(b / 1073741824).toFixed(1)} GB` : `${Math.round(b / 1048576)} MB`);

function StorageCard() {
  const { data, reload } = useApi("/storage/usage");
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const c = data.config;
  const buckets: any[] = data.buckets || [];
  const run = async (dry: boolean) => {
    setBusy(true);
    try {
      const r = await api(`/storage/cleanup?dry_run=${dry}`, { method: "POST" });
      setPreview(dry ? r : null);
      if (!dry) reload();
    } finally { setBusy(false); }
  };
  return (
    <Card title="Dung lượng & tự dọn dẹp" className="mb-4">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
        <div><div className="text-xs text-muted">Video hôm nay</div><div className="font-semibold tnum">{data.today.videos}{c.video_daily_quota ? ` / ${c.video_daily_quota}` : ""}</div><div className="text-xs text-ink2">kho {mb(data.video.bytes)} · {data.video.count} file</div></div>
        <div><div className="text-xs text-muted">Ảnh</div><div className="font-semibold tnum">{mb(data.image.bytes)}</div><div className="text-xs text-ink2">{data.image.count} file</div></div>
        <div><div className="text-xs text-muted">Dữ liệu thô (raw)</div><div className="font-semibold tnum">{mb(data.raw_bytes)}</div></div>
        <div><div className="text-xs text-muted">Đã dọn (giữ ảnh bìa)</div><div className="font-semibold tnum">{data.archived}</div></div>
        <div><div className="text-xs text-muted">Đang ghim 📌</div><div className="font-semibold tnum">{data.pinned}</div></div>
      </div>
      {buckets.length > 0 && (
        <div className="mt-4 space-y-2">
          <div className="text-xs text-muted">Ngân sách kho theo nền tảng — tổng {mb(data.max_bytes)} (R2_MAX_GB), chia bằng <code>STORAGE_SHARES</code>. Một nền tảng không bao giờ chiếm chỗ của nền tảng khác.</div>
          {buckets.map((b) => {
            const pct = b.budget_bytes ? Math.min(1, b.bytes / b.budget_bytes) : 0;
            const over = b.bytes > b.budget_bytes;
            return (
              <div key={b.key} className="grid grid-cols-[150px_1fr_auto] gap-3 items-center text-xs">
                <span className="font-medium truncate">{b.label} <span className="text-muted">{Math.round(b.share * 100)}%</span></span>
                <div className="h-2.5 rounded-full overflow-hidden" style={{ background: "var(--surface-2)" }}>
                  <div className="h-full rounded-full" style={{ width: `${pct * 100}%`, background: over ? "var(--critical)" : pct > 0.85 ? "var(--warning)" : "var(--series-1)" }} />
                </div>
                <span className="tnum text-ink2 whitespace-nowrap">{mb(b.bytes)} / {mb(b.budget_bytes)} · {b.videos} video · {b.images} ảnh · hôm nay {b.videos_today}{b.video_quota ? `/${b.video_quota}` : ""} video{over ? " · vượt → dọn video yếu nhất" : ""}</span>
              </div>
            );
          })}
        </div>
      )}
      <div className="text-xs text-ink2 mt-3 leading-relaxed">
        <b>Video theo lứa mỗi ngày:</b> mỗi ngày lưu tối đa <b>{c.video_daily_quota || "∞"}</b> video, ưu tiên quảng cáo mạnh nhất (chạy lâu, nhiều biến thể, nhiều vị trí) ở thị trường mục tiêu.
        Hết ngày (0h, UTC{c.day_tz_offset >= 0 ? "+" : ""}{c.day_tz_offset}) video cũ hơn <b>{c.video_keep_days || "∞"}</b> ngày bị xoá để nhường chỗ cho lứa mới — lần làm mới tới: <b>{new Date(data.today.next_rollover + "Z").toLocaleString("vi-VN")}</b>.
        Muốn giữ video nào lâu hơn thì ghim 📌 hoặc vote sản phẩm.<br />
        Dọn thêm mỗi 6 giờ: ảnh sau <b>{c.image_days || "∞"}</b> ngày · dữ liệu thô sau <b>{c.raw_days || "∞"}</b> ngày · nhật ký sự kiện sau <b>{c.event_days || "∞"}</b> ngày.
        Thông tin quảng cáo, điểm số, ảnh bìa và lịch sử luôn được giữ. <b>Không bao giờ dọn:</b> video đã ghim, sản phẩm đã vote LOVE/TEST/WATCH, sản phẩm có đơn hàng / experiment / chi tiêu, và quảng cáo lưu bằng extension.
        Đổi bằng biến môi trường <code>VIDEO_DAILY_QUOTA</code>, <code>VIDEO_KEEP_DAYS</code>, <code>DAY_TZ_OFFSET</code>, <code>IMAGE_RETENTION_DAYS</code>, <code>RAW_RETENTION_DAYS</code>, <code>EVENT_RETENTION_DAYS</code> (0 = tắt).
      </div>
      <div className="flex flex-wrap items-center gap-2 mt-3">
        <button className="btn text-xs" onClick={() => run(true)} disabled={busy}>Xem trước sẽ dọn gì</button>
        {preview && (
          <>
            <span className="text-xs text-ink2">Sẽ dọn <b>{preview.archived + (preview.trimmed || 0)}</b> file{preview.trimmed ? ` (${preview.trimmed} do vượt ngân sách nền tảng)` : ""}, giải phóng <b>{mb(preview.freed_bytes)}</b> · giữ lại {preview.kept_protected} file được bảo vệ · xoá {preview.raw_deleted} file raw, {preview.events_deleted} sự kiện cũ</span>
            {preview.archived + (preview.trimmed || 0) + preview.raw_deleted + preview.events_deleted > 0 && <button className="btn btn-primary text-xs" onClick={() => run(false)} disabled={busy}>Dọn ngay</button>}
          </>
        )}
      </div>
    </Card>
  );
}

export default function Collector() {
  const { data, error, reload } = useApi("/collector");
  const [feed, setFeed] = useState<any[]>([]);
  const [q, setQ] = useState({ connector_id: "", query: "", countries: "SA", every_minutes: "60", page_ids: "" });
  const [exp, setExp] = useState<{ source: string; country: string; file: File | null }>({ source: "pipiads", country: "", file: null });
  const [newAdapter, setNewAdapter] = useState("");
  const { byKey } = usePlatforms();
  useEvents((e) => {
    if (["CONNECTOR_SYNCED", "CONNECTOR_FAILED", "SEARCH_DONE", "PRODUCT_DISCOVERED"].includes(e.type)) {
      setFeed((f) => [e, ...f].slice(0, 30));
      if (e.type.startsWith("CONNECTOR")) reload();
    }
  });
  if (!data) return <Loading error={error} />;
  // spy sources grouped by channel (ads / commerce); company data (own ads, CRM, carriers) last
  const grouped: Record<string, any[]> = {};
  data.connectors.forEach((c: any) => {
    const g = INTERNAL.has(c.group) ? "internal" : byKey[c.network]?.channel || "ads";
    (grouped[g] ||= []).push(c);
  });
  const groupOrder: [string, string][] = [...CHANNEL_ORDER, ["internal", "Dữ liệu nội bộ (Company Fit)"]];
  const searchable = data.connectors.filter((c: any) => c.supports_search);

  const addQuery = async () => {
    await api("/collector/queries", { method: "POST", body: JSON.stringify({
      connector_id: Number(q.connector_id || searchable[0]?.id), query: q.query || null,
      page_ids: q.page_ids.split(",").map((x) => x.trim()).filter(Boolean),
      countries: q.countries.split(",").map((x) => x.trim()).filter(Boolean), every_minutes: Number(q.every_minutes || 60), user: getUser().name }) });
    setQ({ ...q, query: "", page_ids: "" }); reload();
  };
  const upload = async () => {
    if (!exp.file) return;
    const fd = new FormData(); fd.append("source", exp.source); if (exp.country) fd.append("country", exp.country); fd.append("file", exp.file);
    await api("/collector/export-upload", { method: "POST", body: fd }); reload();
  };
  const addConnector = async () => {
    if (!newAdapter) return;
    await api("/collector/connectors", { method: "POST", body: JSON.stringify({ adapter: newAdapter, enabled: false }) });
    setNewAdapter(""); reload();
  };

  return (
    <div>
      <PageHeader title="Data & Connectors" subtitle="Unified Spy Collector — nguồn → raw storage → normalize → dedup → product cluster → analytics. App chỉ đọc dữ liệu đã chuẩn hoá.">
        <select className="input" value={newAdapter} onChange={(e) => setNewAdapter(e.target.value)}>
          <option value="">+ Thêm connector…</option>
          {data.catalogue.map((c: any) => <option key={c.adapter} value={c.adapter}>{c.name}</option>)}
        </select>
        <button className="btn" onClick={addConnector} disabled={!newAdapter}>Thêm</button>
      </PageHeader>

      <SourcesCard sources={data.sources || []} byKey={byKey} />
      <StorageCard />
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 mb-4">
        <Card title="Kho dữ liệu">
          <div className="text-sm space-y-1">
            <div className="flex justify-between"><span className="text-ink2">Storage</span><span>{data.storage.backend}{data.storage.data_dir ? ` · ${data.storage.data_dir}` : ""}</span></div>
            <div className="flex justify-between"><span className="text-ink2">Raw batch files</span><span className="tnum">{data.storage.raw_files ?? "R2"}</span></div>
            {Object.entries(data.creatives).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-ink2">Creatives {k}</span><span className="tnum">{v as number}</span></div>)}
            <div className="flex justify-between"><span className="text-ink2">Ingest token</span><span>{data.ingest_token_required ? "bắt buộc" : "chưa đặt (INGEST_TOKEN)"}</span></div>
          </div>
        </Card>
        <Card title="Theo dõi từ khoá / page đối thủ (Tier 2)">
          <div className="grid grid-cols-2 gap-2">
            <select className="input col-span-2" value={q.connector_id} onChange={(e) => setQ({ ...q, connector_id: e.target.value })}>
              {searchable.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <input className="input col-span-2" placeholder="Từ khoá (vd: neck massager)" value={q.query} onChange={(e) => setQ({ ...q, query: e.target.value })} />
            <input className="input col-span-2" placeholder="hoặc Page ID đối thủ (phẩy)" value={q.page_ids} onChange={(e) => setQ({ ...q, page_ids: e.target.value })} />
            <input className="input" placeholder="SA,AE" value={q.countries} onChange={(e) => setQ({ ...q, countries: e.target.value.toUpperCase() })} />
            <input className="input" placeholder="Mỗi N phút" value={q.every_minutes} onChange={(e) => setQ({ ...q, every_minutes: e.target.value.replace(/\D/g, "") })} />
          </div>
          <button className="btn btn-primary mt-2 text-xs" onClick={addQuery} disabled={!q.query && !q.page_ids}>Thêm theo dõi</button>
        </Card>
        <Card title="Upload file export (Pipiads / Minea / BigSpy)">
          <div className="grid grid-cols-2 gap-2">
            <input className="input" placeholder="Nguồn (pipiads)" value={exp.source} onChange={(e) => setExp({ ...exp, source: e.target.value })} />
            <input className="input" placeholder="Country mặc định" value={exp.country} onChange={(e) => setExp({ ...exp, country: e.target.value.toUpperCase() })} />
            <input type="file" accept=".csv,.xlsx,.json" className="text-xs col-span-2" onChange={(e) => setExp({ ...exp, file: e.target.files?.[0] || null })} />
          </div>
          <button className="btn btn-primary mt-2 text-xs" onClick={upload} disabled={!exp.file}>Upload & xử lý</button>
          <div className="text-[11px] text-muted mt-2">Cột được nhận diện tự động (Ad ID, Advertiser, Caption, Video URL, Landing page, Country, First seen, Likes…). Video trong file sẽ được tải về kho.</div>
        </Card>
      </div>

      <Card title="Connectors" pad={false} className="mb-4">
        {groupOrder.filter(([g]) => grouped[g]?.length).map(([g, title]) => (
          <div key={g}>
            <div className="px-4 pt-3 pb-1 text-[11px] uppercase tracking-wider text-muted">{title} · {grouped[g].length}</div>
            {grouped[g].map((c) => <ConnectorRow key={c.id} c={c} onChange={reload} byKey={byKey} />)}
          </div>
        ))}
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Tracked queries" pad={false}>
          <table className="data"><thead><tr><th>Từ khoá / page</th><th>Market</th><th>Chu kỳ</th><th>Lần chạy</th><th>Kết quả</th><th></th></tr></thead>
            <tbody>{data.tracked_queries.map((t: any) => (
              <tr key={t.id}>
                <td>{t.query || `pages: ${t.page_ids.join(",")}`}</td><td>{t.countries.join(",")}</td><td>{t.every_minutes}′</td>
                <td className="text-xs">{fmt.dt(t.last_run_at)}</td>
                <td className="text-xs">{t.last_error ? <span style={{ color: "var(--critical)" }}>{t.last_error.slice(0, 80)}</span> : `${t.last_count} ads · ${t.last_new} mới`}</td>
                <td className="whitespace-nowrap">
                  <button className="btn text-[11px] px-2 py-0.5" onClick={async () => { await api(`/collector/queries/${t.id}/run`, { method: "POST" }); }}>Chạy</button>{" "}
                  <button className="btn text-[11px] px-2 py-0.5" onClick={async () => { await api(`/collector/queries/${t.id}`, { method: "DELETE" }); reload(); }}>Xoá</button>
                </td>
              </tr>
            ))}</tbody></table>
        </Card>
        <Card title="Realtime feed">
          {feed.length === 0 ? <div className="text-sm text-muted">Chờ sự kiện…</div> : (
            <ul className="text-xs space-y-1 max-h-72 overflow-y-auto">
              {feed.map((e, i) => <li key={i}><b>{e.type}</b> {new Date(e.at).toLocaleTimeString("vi-VN")} — {JSON.stringify(e.data).slice(0, 180)}</li>)}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Đẩy dữ liệu vào (Ingest API & Webhooks)" className="mt-4">
        <div className="text-xs space-y-2 text-ink2">
          <p>Header <code>X-Ingest-Token: $INGEST_TOKEN</code>. Mọi nguồn dùng chung một schema (Common Data Contract); trường không có thì để <code>null</code>.</p>
          <pre className="p-3 rounded-lg overflow-x-auto" style={{ background: "var(--surface-2)" }}>{`POST /api/ingest/ads        {"records":[{"source":"pipiads","source_ad_id":"123","platform":"tiktok","country":"SA",
                              "advertiser":"ABC Store","ad_text":"…","cta_type":"SHOP_NOW","landing_page":"https://…",
                              "media":[{"type":"video","url":"https://…mp4","preview_url":"https://…jpg"}],
                              "first_seen":"2026-10-01","active":true,"likes":500,"comments":20,"shares":10}]}
POST /api/ingest/products   {"name":"…","url":"…","country":"SA","video_urls":["…"],"saved_by":"mkt.a"}   ← Chrome extension
POST /api/webhooks/orders   {"records":[{"external_id":"ORD1","product_code":"PRD_0000012","status":"confirmed","amount":149,
                              "campaign_id":"…","ad_external_id":"…","sales_agent":"…"}]}                 ← Pancake / CRM
POST /api/webhooks/shipments {"records":[{"tracking_code":"SPX123","carrier":"aramex","carrier_status":"RTO_CUSTOMER_REJECT"}]}
POST /api/webhooks/comments {"records":[{"product_code":"…","text":"…","source":"inbox"}]}
POST /api/webhooks/ad-metrics {"records":[{"date":"2026-10-05","ad_id":"…","campaign_name":"PRD_0000012 SA","spend":42.5,"leads":31}]}`}</pre>
          <p>Push sources: {Object.entries(data.push_sources).map(([k, v]) => `${k} (${v})`).join(" · ")}</p>
        </div>
      </Card>
    </div>
  );
}
