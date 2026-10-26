"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import { Loading } from "@/components/ui";

/** Old product-list routes (merged into /radar, design §3b): keep their query string and land on the matching view. */
function Go({ view }: { view: string }) {
  const router = useRouter();
  const sp = useSearchParams();
  useEffect(() => {
    const q = new URLSearchParams(sp.toString());
    q.set("view", view);
    router.replace(`/radar?${q}`);
  }, [router, sp, view]);
  return <Loading />;
}

export function RedirectToView({ view }: { view: string }) {
  return <Suspense fallback={<Loading />}><Go view={view} /></Suspense>;
}
