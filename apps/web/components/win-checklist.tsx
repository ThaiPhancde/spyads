import { cn } from "@/lib/utils";

/** Win checklist (design §2.4): 4 items, each ✔ / ✘ / ? + the number behind it. Symbol and text, never colour alone. */
const MARK: Record<string, [string, string, string]> = {
  pass: ["✔", "đạt", "text-good-text"],
  fail: ["✘", "không đạt", "text-critical"],
  unknown: ["?", "chưa biết", "text-muted-foreground"],
};

export function WinChecklist({ items, compact }: { items?: any[]; compact?: boolean }) {
  if (!items?.length) return null;
  if (compact) {
    return (
      <ul className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px]" aria-label="Checklist win">
        {items.map((i) => {
          const [sym, word, tone] = MARK[i.status] || MARK.unknown;
          return (
            <li key={i.key} title={`${i.label}: ${word}${i.value ? ` · ${i.value}` : ""} — ${i.detail}`}>
              <span aria-hidden className={cn("font-semibold", tone)}>{sym}</span> {i.label}<span className="sr-only">: {word}</span>
            </li>
          );
        })}
      </ul>
    );
  }
  return (
    <ul className="space-y-2.5 text-sm" aria-label="Checklist win">
      {items.map((i) => {
        const [sym, word, tone] = MARK[i.status] || MARK.unknown;
        return (
          <li key={i.key} className="flex gap-2">
            <span aria-hidden className={cn("w-4 shrink-0 text-center font-semibold", tone)}>{sym}</span>
            <div className="min-w-0">
              <div><b>{i.label}</b> <span className={cn("text-xs", tone)}>({word})</span>{i.value && <span className="tnum"> · {i.value}</span>}</div>
              <div className="text-xs text-muted-foreground">{i.detail}</div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
