"use client";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

export function ThemeToggle() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const Icon = !mounted ? Sun : resolvedTheme === "dark" ? Moon : Sun;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Đổi giao diện sáng / tối"><Icon /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {([["light", "Sáng", Sun], ["dark", "Tối", Moon], ["system", "Theo hệ thống", Monitor]] as const).map(([k, l, I]) => (
          <DropdownMenuItem key={k} onClick={() => setTheme(k)} className={theme === k ? "font-semibold" : undefined}><I />{l}</DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
