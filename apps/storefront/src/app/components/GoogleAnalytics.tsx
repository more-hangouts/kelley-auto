import Script from "next/script";

/**
 * Google tag (gtag.js / GA4). Loads on every route because it's rendered from
 * the root layout. The measurement ID comes from NEXT_PUBLIC_GA_ID (baked at
 * build time); when it's unset the component renders nothing, so analytics
 * stays off in any environment that doesn't configure it.
 *
 * `afterInteractive` is the recommended strategy for analytics in the Next.js
 * App Router — the tag loads right after the page becomes interactive.
 */
export default function GoogleAnalytics() {
  const gaId = process.env.NEXT_PUBLIC_GA_ID;
  if (!gaId) return null;

  return (
    <>
      <Script
        src={`https://www.googletagmanager.com/gtag/js?id=${gaId}`}
        strategy="afterInteractive"
      />
      <Script id="gtag-init" strategy="afterInteractive">
        {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', '${gaId}');`}
      </Script>
    </>
  );
}
