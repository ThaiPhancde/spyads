"use client";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

const ALL = "__all";

/** shadcn Select over a flat option list; "" (= all / unset) is allowed as a value, unlike Radix. */
export function SimpleSelect({ value, onChange, options, placeholder, className, size = "sm" }: {
  value: string; onChange: (v: string) => void; options: (string | [string, string])[]; placeholder?: string; className?: string; size?: "sm" | "default";
}) {
  const opts = options.map((o) => (typeof o === "string" ? [o, o] : o));
  return (
    <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? "" : v)}>
      <SelectTrigger size={size} className={className} aria-label={placeholder}><SelectValue placeholder={placeholder} /></SelectTrigger>
      <SelectContent>
        {placeholder && <SelectItem value={ALL}>{placeholder}</SelectItem>}
        {opts.map(([v, l]) => v && <SelectItem key={v} value={v}>{l}</SelectItem>)}
      </SelectContent>
    </Select>
  );
}
