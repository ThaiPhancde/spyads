import "./globals.css";
import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";
import { AppSidebar } from "@/components/layout/app-sidebar";
import { RealtimeToasts } from "@/components/layout/realtime-toasts";
import { SiteHeader } from "@/components/layout/site-header";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "ToolSpy — Market Intelligence OS",
  description: "Spy Ads + Product Intelligence + Internal Business Intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <body>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <SidebarProvider style={{ "--sidebar-width": "16.5rem", "--header-height": "3rem" } as React.CSSProperties}>
            <AppSidebar variant="inset" />
            <SidebarInset>
              <SiteHeader />
              <main className="flex min-w-0 flex-1 flex-col gap-4 p-4 md:p-6">{children}</main>
            </SidebarInset>
          </SidebarProvider>
          <Toaster position="bottom-right" closeButton />
          <RealtimeToasts />
        </ThemeProvider>
      </body>
    </html>
  );
}
