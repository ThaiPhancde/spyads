import "./globals.css";
import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { RealtimeToasts } from "@/components/layout/realtime-toasts";
import { SiteHeader } from "@/components/layout/site-header";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";
import { SessionShell } from "@/components/workspace/session";

export const metadata: Metadata = {
  title: "ToolSpy — Market Intelligence OS",
  description: "Spy Ads + Product Intelligence + Internal Business Intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <SessionShell>{children}</SessionShell>
          <Toaster position="bottom-right" closeButton />
        </ThemeProvider>
      </body>
    </html>
  );
}
