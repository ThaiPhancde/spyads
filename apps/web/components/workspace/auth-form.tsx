"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export function AuthForm({ mode }: { mode: "login" | "register" | "forgot-password" | "reset-password" }) {
  const [error, setError] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api("/auth/csrf").catch((e) => setError(e.message)); }, [mode]);
  const title = { login: "Đăng nhập ToolSpy", register: "Tạo tài khoản MKT", "forgot-password": "Quên mật khẩu", "reset-password": "Đặt lại mật khẩu" }[mode];
  return <div className="w-full max-w-md space-y-5 rounded-xl border bg-card p-7 shadow-sm">
    <div><p className="mb-2 text-sm font-semibold text-primary">TOOLSPY</p><h1 className="text-2xl font-semibold">{title}</h1></div>
    <form className="space-y-4" onSubmit={async (e) => {
      e.preventDefault(); setBusy(true); setError(""); setMessage("");
      const form = new FormData(e.currentTarget); const body = Object.fromEntries(form.entries());
      try {
        const d = await api(`/auth/${mode}`, { method: "POST", body: JSON.stringify(body) });
        if (mode === "login") { sessionStorage.removeItem("mi_search"); location.assign("/workspace/"); }
        else setMessage(d.message || "Mật khẩu đã được cập nhật. Bạn có thể đăng nhập.");
      } catch (e: any) { setError(e.message); } finally { setBusy(false); }
    }}>
      {mode === "register" && <label className="block space-y-1 text-sm">Tên<Input name="name" required maxLength={100} autoComplete="name" /></label>}
      {<label className="block space-y-1 text-sm">Email<Input name="email" type="email" required autoComplete="email" /></label>}
      {mode === "reset-password" && <label className="block space-y-1 text-sm">Mã OTP (6 số)<Input name="otp" required inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" /><span className="text-xs text-muted-foreground">Mã được gửi tới email, hiệu lực 10 phút, dùng một lần.</span></label>}
      {mode !== "forgot-password" && <label className="block space-y-1 text-sm">{mode === "reset-password" ? "Mật khẩu mới" : "Mật khẩu"}<Input name="password" type="password" required minLength={mode === "login" ? 1 : 6} maxLength={128} autoComplete={mode === "login" ? "current-password" : "new-password"} />{mode !== "login" && <span className="text-xs text-muted-foreground">Từ 6 đến 128 ký tự.</span>}</label>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}{message && <p role="status" className="text-sm">{message}</p>}
      <Button type="submit" className="w-full" disabled={busy}>{busy ? "Đang xử lý…" : title}</Button>
    </form>
    <div className="flex flex-wrap gap-4 text-sm underline">{mode !== "login" && <Link href="/login">Đăng nhập</Link>}{mode === "forgot-password" && <Link href="/reset-password">Đã có mã OTP</Link>}{mode === "login" && <><Link href="/register">Đăng ký</Link><Link href="/forgot-password">Quên mật khẩu</Link></>}</div>
  </div>;
}
