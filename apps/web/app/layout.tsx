import "./globals.css";
import type { Metadata } from "next";
import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "Market Intelligence OS",
  description: "Spy Ads + Product Intelligence + Internal Business Intelligence",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 min-w-0 px-4 md:px-8 py-6 max-w-[1500px]">{children}</main>
        </div>
      </body>
    </html>
  );
}
