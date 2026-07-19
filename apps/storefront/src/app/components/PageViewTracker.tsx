"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { track } from "@/lib/analytics";

/**
 * Fires a first-party `page_view` beacon on every App Router navigation. Mounted
 * once from the root layout. Client-only and best-effort — it renders nothing.
 */
export default function PageViewTracker() {
  const pathname = usePathname();

  useEffect(() => {
    track("page_view");
  }, [pathname]);

  return null;
}
