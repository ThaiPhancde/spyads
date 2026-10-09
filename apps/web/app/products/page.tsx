"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ArrowDownUp, ArrowRight, Merge } from "lucide-react";
import { toast } from "sonner";
import { SimpleSelect } from "@/components/simple-select";
import { Card, Loading, PageHeader, ProductTable } from "@/components/ui";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card as UiCard, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { api, qs, useApi } from "@/lib/api";

const SATURATION = ["Low Saturation", "Growing", "Competitive", "Highly Saturated", "Declining"];
const SORTS: [string, string][] = [["opportunity_score", "Opportunity"], ["win_score", "Win"], ["rare_winner_score", "Rare winner"], ["rarity_score", "Rarity"], ["saturation_score", "Saturation"], ["confidence_score", "Confidence"], ["growth_7d", "Growth 7d"], ["advertisers", "Advertisers"], ["active_ads", "Active ads"]];

function Explorer() {
  const sp = useSearchParams();
  const meta = useApi("/meta");
  const [f, setF] = useState<any>({
    q: "", country: sp.get("country") || "", category: sp.get("category") || "", recommendation: sp.get("recommendation") || "",
    lifecycle: "", saturation_state: "", min_win: "", max_advertisers: "", min_confidence: "", sort: "opportunity_score", order: "desc",
  });
  const { data, error } = useApi(`/products${qs({ ...f, limit: 300 })}`);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const setV = (k: string) => (v: string) => setF({ ...f, [k]: v });

  const [merge, setMerge] = useState({ source_id: "", target_id: "" });
  const doMerge = async () => {
    try {
      await api("/products/merge", { method: "POST", body: JSON.stringify({ source_id: +merge.source_id, target_id: +merge.target_id }) });
      toast.success("Đã gộp sản phẩm. Tải lại trang để xem.");
    } catch (e: any) { toast.error(`Lỗi: ${e.message}`); }
  };

  const m = meta.data;
  return (
    <div>
      <PageHeader title="Product Explorer" subtitle="Search / filter toàn bộ sản phẩm (Product là entity trung tâm)" />
      <UiCard className="mb-4 py-3">
        <CardContent className="flex flex-wrap items-center gap-2 px-3">
          <Input className="h-8 w-56" placeholder="Tên, alias hoặc PRD_…" value={f.q} onChange={set("q")} />
          <NativeSelect size="sm" value={f.country} onChange={set("country")}><NativeSelectOption value="">Country</NativeSelectOption>{m?.countries.map((c: string) => <NativeSelectOption key={c}>{c}</NativeSelectOption>)}</NativeSelect>
          <NativeSelect size="sm" value={f.category} onChange={set("category")}><NativeSelectOption value="">Category</NativeSelectOption>{m?.categories.map((c: string) => <NativeSelectOption key={c}>{c}</NativeSelectOption>)}</NativeSelect>
          <SimpleSelect value={f.recommendation} onChange={setV("recommendation")} placeholder="Action" options={m?.recommendations || []} />
          <SimpleSelect value={f.lifecycle} onChange={setV("lifecycle")} placeholder="Lifecycle" options={m?.lifecycle_states || []} />
          <SimpleSelect value={f.saturation_state} onChange={setV("saturation_state")} placeholder="Saturation" options={SATURATION} />
          <Input className="h-8 w-24" placeholder="Win ≥" value={f.min_win} onChange={set("min_win")} />
          <Input className="h-8 w-28" placeholder="Advertisers ≤" value={f.max_advertisers} onChange={set("max_advertisers")} />
          <Input className="h-8 w-28" placeholder="Confidence ≥" value={f.min_confidence} onChange={set("min_confidence")} />
          <SimpleSelect value={f.sort} onChange={setV("sort")} className="w-44" options={SORTS.map(([v, l]) => [v, `Sort: ${l}`])} />
          <Button variant="outline" size="sm" className="text-xs" onClick={() => setF({ ...f, order: f.order === "desc" ? "asc" : "desc" })}><ArrowDownUp />{f.order === "desc" ? "Giảm dần" : "Tăng dần"}</Button>
        </CardContent>
      </UiCard>
      {!data ? <Loading error={error} /> : (
        <Card title={`${data.total} sản phẩm`} pad={false}><ProductTable rows={data.rows} /></Card>
      )}
      <Card title="Entity resolution — gộp thủ công 2 sản phẩm trùng" className="mt-4">
        <p className="mb-2 text-xs text-ink2">Khi title/landing khác nhau quá nhiều (vd. “Handheld Car Vacuum” vs “Cordless Handheld Vacuum Cleaner”), gộp sản phẩm nguồn vào sản phẩm đích; toàn bộ ads, reviews, stores được chuyển sang.</p>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="h-8 w-36" placeholder="Source product id" value={merge.source_id} onChange={(e) => setMerge({ ...merge, source_id: e.target.value })} />
          <ArrowRight className="size-4 text-muted-foreground" />
          <Input className="h-8 w-36" placeholder="Target product id" value={merge.target_id} onChange={(e) => setMerge({ ...merge, target_id: e.target.value })} />
          <AlertDialog>
            <AlertDialogTrigger asChild><Button size="sm" disabled={!merge.source_id || !merge.target_id}><Merge />Gộp</Button></AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Gộp sản phẩm #{merge.source_id} vào #{merge.target_id}?</AlertDialogTitle>
                <AlertDialogDescription>Toàn bộ ads, reviews, stores của sản phẩm nguồn sẽ chuyển sang sản phẩm đích. Không hoàn tác được.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Huỷ</AlertDialogCancel>
                <AlertDialogAction onClick={doMerge}>Gộp</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </Card>
    </div>
  );
}

export default function Page() {
  return <Suspense fallback={<Loading />}><Explorer /></Suspense>;
}
