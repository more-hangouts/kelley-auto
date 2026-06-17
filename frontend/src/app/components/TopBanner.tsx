import Link from "next/link";
import { getSiteSettings } from "@/lib/api";

export default async function TopBanner() {
  const settings = await getSiteSettings();
  const phone = settings.phone || "(123) 333-1212";
  const city = settings.city || "Your City, ST";
  const bannerLabel = settings.bannerLabel || "Cash Only";
  const bannerText = settings.bannerText || "Quality pre-owned vehicles at honest prices";
  const telHref = `tel:+1${phone.replace(/\D/g, "")}`;

  return (
    <div className="bg-neutral-800 flex items-center justify-center md:justify-between px-5 md:px-10 py-2.5 md:py-3 text-sm text-white">
      {/* Left — phone number */}
      <div className="hidden md:flex items-center gap-1.5">
        <svg className="size-4 shrink-0" fill="none" viewBox="0 0 18 18">
          <path
            d="M16.46 12.83v2.25a1.5 1.5 0 0 1-1.64 1.5 14.85 14.85 0 0 1-6.47-2.3 14.63 14.63 0 0 1-4.5-4.5A14.85 14.85 0 0 1 1.52 3.27 1.5 1.5 0 0 1 3 1.63h2.25a1.5 1.5 0 0 1 1.5 1.29 9.63 9.63 0 0 0 .53 2.1 1.5 1.5 0 0 1-.34 1.58l-.95.95a12 12 0 0 0 4.5 4.5l.95-.95a1.5 1.5 0 0 1 1.58-.34 9.63 9.63 0 0 0 2.1.53 1.5 1.5 0 0 1 1.29 1.52Z"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <a href={telHref} className="hover:text-primary transition-colors">
          {phone}
        </a>
      </div>

      {/* Center — promo message */}
      <div className="flex items-center gap-4 md:gap-6">
        <p className="text-xs md:text-sm">
          <span className="font-semibold text-primary">{bannerLabel}</span>
          {" — "}
          {bannerText}
        </p>
        <span className="hidden md:inline text-neutral-400">|</span>
        <Link
          href="/shop"
          className="hidden md:inline underline text-xs md:text-sm hover:text-primary transition-colors"
        >
          View Inventory
        </Link>
      </div>

      {/* Right — location */}
      <div className="hidden md:flex items-center gap-1.5 text-neutral-300">
        <svg className="size-4 shrink-0" fill="none" viewBox="0 0 18 18">
          <path
            d="M9 1.5C6.51 1.5 4.5 3.51 4.5 6c0 3.75 4.5 10.5 4.5 10.5S13.5 9.75 13.5 6c0-2.49-2.01-4.5-4.5-4.5ZM9 7.5a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3Z"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span>{city}</span>
      </div>
    </div>
  );
}
