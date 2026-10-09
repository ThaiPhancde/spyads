"use client";
import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export const splitTags = (s: string) => s.split(/[|,\n]/).map((x) => x.trim()).filter(Boolean);

/** Gõ từ khoá → Enter (hoặc dấu phẩy) thành tag; × để xoá; Backspace ở ô trống xoá tag cuối; Ctrl+Enter = tìm trực tiếp. */
export function TagInput({ tags, setTags, draft, setDraft, placeholder, tone = "default", onSubmit, className }: {
  tags: string[]; setTags: (t: string[]) => void; draft: string; setDraft: (s: string) => void; placeholder: string;
  tone?: "default" | "destructive"; onSubmit?: (live: boolean) => void; className?: string }) {
  const add = (s: string) => { const n = splitTags(s).filter((x) => !tags.some((t) => t.toLowerCase() === x.toLowerCase())); if (n.length) setTags([...tags, ...n]); setDraft(""); };
  return (
    <div className={cn("flex min-h-9 min-w-[240px] flex-1 flex-wrap items-center gap-1 rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-xs transition-[color,box-shadow] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50 dark:bg-input/30", className)}>
      {tags.map((t) => (
        <Badge key={t} variant={tone === "destructive" ? "outline" : "secondary"} className={cn("gap-1 pr-1", tone === "destructive" ? "border-critical/40 text-critical" : "border-series-1/60")}>
          {t}
          <button type="button" aria-label={`Xoá ${t}`} onClick={() => setTags(tags.filter((x) => x !== t))} className="rounded-full hover:bg-muted">
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <input className="min-w-[140px] flex-1 bg-transparent outline-none placeholder:text-muted-foreground" value={draft} placeholder={tags.length ? "+ Thêm từ khoá" : placeholder}
             onChange={(e) => e.target.value.includes(",") ? add(e.target.value) : setDraft(e.target.value)}
             onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/[|,\n]/.test(t)) { e.preventDefault(); add(draft + t); } }}
             onKeyDown={(e) => {
               if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); onSubmit?.(true); }
               else if (e.key === "Enter" && draft.trim()) { e.preventDefault(); add(draft); }
               else if (e.key === "Enter" && tags.length) { e.preventDefault(); onSubmit?.(false); }
               else if (e.key === "Backspace" && !draft && tags.length) setTags(tags.slice(0, -1));
             }} />
    </div>
  );
}
