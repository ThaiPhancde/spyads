"use client";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Card, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AdMatchRow } from "@/components/eu-market/matches";
import { EuLoading, NoData } from "@/components/eu-market/shared";
import { api, qs, useAction, useApi } from "@/lib/api";

export default function EuAdMatches() {
  const [status, setStatus] = useState("pending");
  const { data, error, reload } = useApi<any>(`/eu-market/ad-matches${qs({ status: status === "all" ? "" : status, limit: 300 })}`);
  const act = useAction();
  const review = (id: number, s: string) => act.run(async () => { await api(`/eu-market/ad-matches/${id}/review`, { method: "POST", body: JSON.stringify({ status: s }) }); reload(); });
  const rematch = () => act.run(async () => { await api("/eu-market/admin/match", { method: "POST" }); setTimeout(reload, 4000); });
  return (
    <div className="space-y-4">
      <PageHeader title="Ad Matches" subtitle="Liên kết sản phẩm EU với quảng cáo đã có trong kho SpyAds (Meta / TikTok…). Không tải video mới.">
        <Button size="sm" variant="outline" onClick={rematch} disabled={act.busy}><RefreshCw />Chạy ghép lại</Button>
      </PageHeader>
      <Tabs value={status} onValueChange={setStatus}>
        <TabsList><TabsTrigger value="pending">Cần review</TabsTrigger><TabsTrigger value="confirmed">Đã xác nhận</TabsTrigger>
          <TabsTrigger value="rejected">Đã loại</TabsTrigger><TabsTrigger value="all">Tất cả</TabsTrigger></TabsList>
      </Tabs>
      {act.error && <p className="text-sm text-critical">{act.error}</p>}
      {!data ? <EuLoading error={error} /> : (
        <Card description={data.note}>
          {data.items.length ? data.items.map((m: any) => <AdMatchRow key={m.id} m={m} busy={act.busy} onReview={(s) => review(m.id, s)} />)
            : <NoData>Không có match ở trạng thái này. Ghép chạy sau mỗi lần đồng bộ, hoặc bấm “Chạy ghép lại”. Nguồn dữ liệu:</NoData>}
        </Card>
      )}
    </div>
  );
}
