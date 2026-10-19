"use client";
import { Button } from "@/components/ui/button";

export const EVENT_LABELS: Record<string, string> = {
  new_ad: "Phát hiện Ads mới", ad_changed: "Quảng cáo thay đổi", ad_inactive: "Nguồn xác nhận Ads dừng",
  ad_unobserved: "Không tìm thấy Ads · trạng thái chưa xác định", scan_complete: "Hoàn tất kiểm tra", follow: "Bắt đầu theo dõi", source_error: "Nguồn lỗi kéo dài",
  login: "Đăng nhập", logout: "Đăng xuất", unfollow: "Bỏ theo dõi", ad_interaction: "Cập nhật Ads cá nhân", registered: "Đăng ký",
};
export const STATUS_LABELS: Record<string, string> = { pending: "Đang chờ", running: "Đang kiểm tra", success: "Thành công", partial: "Dữ liệu một phần", unsupported: "Nguồn chưa hỗ trợ", not_configured: "Thiếu cấu hình", error: "Nguồn gặp lỗi", timeout: "Quá thời gian chờ", quota_wait: "Chờ quota", not_scheduled: "Chưa lên lịch" };
export function Pager({ offset, total, size = 40, onChange }: { offset: number; total: number; size?: number; onChange: (n: number) => void }) {
  return <div className="mt-4 flex items-center gap-3 text-sm"><Button size="sm" variant="outline" disabled={!offset} onClick={() => onChange(Math.max(0, offset - size))}>Trang trước</Button>
    <span>{total ? offset + 1 : 0}–{Math.min(offset + size, total)} / {total}</span><Button size="sm" variant="outline" disabled={offset + size >= total} onClick={() => onChange(offset + size)}>Trang sau</Button></div>;
}
