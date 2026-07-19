"use client";

import Script from "next/script";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { META_PIXEL_ID, fbqTrack } from "@/lib/metaPixel";

/**
 * Meta Pixel loader. Rendered once from the root layout; renders nothing and
 * loads nothing when NEXT_PUBLIC_META_PIXEL_ID is unset (baked at build time,
 * same pattern as GoogleAnalytics).
 *
 * The init snippet fires the initial PageView; App Router client-side
 * navigations don't reload the page, so the pathname effect fires PageView on
 * every subsequent route change (skipping the first render to avoid double
 * counting the landing page).
 */
export default function MetaPixel() {
  const pathname = usePathname();
  const first = useRef(true);

  useEffect(() => {
    if (!META_PIXEL_ID) return;
    if (first.current) {
      first.current = false; // initial PageView comes from the init snippet
      return;
    }
    fbqTrack("PageView");
  }, [pathname]);

  if (!META_PIXEL_ID) return null;

  return (
    <>
      <Script id="meta-pixel" strategy="afterInteractive">
        {`!function(f,b,e,v,n,t,s)
{if(f.fbq)return;n=f.fbq=function(){n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)};
if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';
n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];
s.parentNode.insertBefore(t,s)}(window, document,'script',
'https://connect.facebook.net/en_US/fbevents.js');
fbq('init', '${META_PIXEL_ID}');
fbq('track', 'PageView');`}
      </Script>
      <noscript>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          height="1"
          width="1"
          style={{ display: "none" }}
          alt=""
          src={`https://www.facebook.com/tr?id=${META_PIXEL_ID}&ev=PageView&noscript=1`}
        />
      </noscript>
    </>
  );
}
