"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "@/components/workspace/session";
import { Card, PageHeader } from "@/components/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function Account() {
  const { user, profile, reload } = useSession(); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  return <div className="max-w-2xl space-y-5"><PageHeader title="Tài khoản của tôi" subtitle={`${user?.email} · ${user?.role}`} />{message && <p role="status" className="text-sm">{message}</p>}
    <Card title="Hồ sơ cá nhân"><form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); setBusy(true); const body = Object.fromEntries(new FormData(e.currentTarget)); try { await api("/auth/profile", { method: "PATCH", body: JSON.stringify(body) }); setMessage("Đã cập nhật hồ sơ"); await reload(); } catch (e: any) { setMessage(e.message); } finally { setBusy(false); } }}>
      <label className="block text-sm">Tên<Input name="name" defaultValue={user?.name} required maxLength={100} /></label><label className="block text-sm">Giới thiệu<textarea name="bio" defaultValue={profile.bio} maxLength={1000} className="block w-full rounded border bg-background p-2" /></label>
      <label className="block text-sm">Nhóm <select name="team" defaultValue={profile.team} className="rounded border bg-background p-2"><option value="">Tất cả</option><option value="mess">MKT Mess</option><option value="ladi">MKT Ladi</option></select></label><Button disabled={busy}>Lưu hồ sơ</Button></form></Card>
    <Card title="Đổi mật khẩu"><form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); const form = e.currentTarget; const body = Object.fromEntries(new FormData(form)); setBusy(true); try { await api("/auth/password", { method: "POST", body: JSON.stringify(body) }); setMessage("Đã đổi mật khẩu và đăng xuất các phiên khác"); form.reset(); } catch (e: any) { setMessage(e.message); } finally { setBusy(false); } }}>
      <label className="block text-sm">Mật khẩu hiện tại<Input name="current_password" type="password" autoComplete="current-password" required /></label><label className="block text-sm">Mật khẩu mới<Input name="new_password" type="password" autoComplete="new-password" required minLength={6} maxLength={128} /></label><Button disabled={busy}>Đổi mật khẩu</Button></form></Card>
  </div>;
}
