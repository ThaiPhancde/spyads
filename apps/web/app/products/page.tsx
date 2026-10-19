"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ArrowDownUp } from "lucide-react";
import { ClusterButtons } from "@/components/cluster-dialog";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Loading, PageHeader, ProductTable } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Card as UiCard, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { qs, useApi } from "@/lib/api";

const SATURATION = ["Low Saturation", "Growing", "Competitive", "Highly Saturated", "Declining"];
const SORTS: [string, string][] = [["opportunity_score", "Opportunity"], ["rarity_score", "Rarity"], ["saturation_score", "Saturation"], ["confidence_score", "Confidence"], ["growth_7d", "Growth 7d"], ["advertisers", "Advertisers"], ["active_ads", "Active ads"]];

function Explorer() {
  const sp = useSearchParams();
  const meta = useApi("/meta");
  const [f, setF] = useState<any>({
    q: "", country: sp.get("country") || "", category: sp.get("category") || "", recommendation: sp.get("recommendation") || "",
    lifecycle: "", saturation_state: "", max_advertisers: "", min_confidence: "", sort: "opportunity_score", order: "desc",
  });
  const { data, error, reload } = useApi(`/products${qs({ ...f, limit: 300 })}`);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const setV = (k: string) => (v: string) => setF({ ...f, [k]: v });

  const m = meta.data;
  return (
    <div>
      <PageHeader title="Product Explorer" subtitle="Search / filter toàn bộ sản phẩm. Gộp / Tách ở cuối mỗi dòng; gợi ý gộp tự động ở mục Gom cụm sản phẩm." />
      <UiCard className="mb-4 py-3">
        <CardContent className="flex flex-wrap items-center gap-2 px-3">
          <Input className="h-8 w-56" placeholder="Tên, alias hoặc PRD_…" value={f.q} onChange={set("q")} />
          <NativeSelect size="sm" value={f.country} onChange={set("country")}><NativeSelectOption value="">Country</NativeSelectOption>{m?.countries.map((c: string) => <NativeSelectOption key={c}>{c}</NativeSelectOption>)}</NativeSelect>
          <NativeSelect size="sm" value={f.category} onChange={set("category")}><NativeSelectOption value="">Category</NativeSelectOption>{m?.categories.map((c: string) => <NativeSelectOption key={c}>{c}</NativeSelectOption>)}</NativeSelect>
          <SimpleSelect value={f.recommendation} onChange={setV("recommendation")} placeholder="Action" options={m?.recommendations || []} />
          <SimpleSelect value={f.lifecycle} onChange={setV("lifecycle")} placeholder="Lifecycle" options={m?.lifecycle_states || []} />
          <SimpleSelect value={f.saturation_state} onChange={setV("saturation_state")} placeholder="Saturation" options={SATURATION} />
          <Input className="h-8 w-28" placeholder="Advertisers ≤" value={f.max_advertisers} onChange={set("max_advertisers")} />
          <Input className="h-8 w-28" placeholder="Confidence ≥" value={f.min_confidence} onChange={set("min_confidence")} />
          <SimpleSelect value={f.sort} onChange={setV("sort")} className="w-44" options={SORTS.map(([v, l]) => [v, `Sort: ${l}`])} />
          <Button variant="outline" size="sm" className="text-xs" onClick={() => setF({ ...f, order: f.order === "desc" ? "asc" : "desc" })}><ArrowDownUp />{f.order === "desc" ? "Giảm dần" : "Tăng dần"}</Button>
        </CardContent>
      </UiCard>
      {!data ? <Loading error={error} /> : (
        <Card title={`${data.total} sản phẩm`} pad={false}><ProductTable rows={data.rows} renderActions={(r) => <ClusterButtons p={r} onChange={reload} />} /></Card>
      )}
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Explorer /></Suspense>;
}
