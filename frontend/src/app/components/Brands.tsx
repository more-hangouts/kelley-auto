import Link from "next/link";
import Image from "next/image";
import type { PayloadVehicle } from "@/types/vehicle";

// Manufacturer badges we ship locally (public/images/brands/<slug>.png).
// Shown for the makes we carry — standard nominative use. Any make without a
// badge here falls back to its initial letter, so new inventory never breaks.
const BRAND_LOGOS = new Set([
  "audi", "buick", "cadillac", "chevrolet", "chrysler", "dodge", "ford",
  "gmc", "hummer", "infiniti", "kia", "mazda", "nissan", "volvo",
]);

const brandSlug = (make: string) =>
  make.toLowerCase().replace(/[^a-z0-9]+/g, "");

export default function Brands({ vehicles }: { vehicles: PayloadVehicle[] }) {
  // Extract unique makes from actual inventory (available vehicles first)
  const available = vehicles.filter((v) => v.status !== "SOLD");
  const makeSet = new Set<string>();
  available.forEach((v) => {
    if (v.make) makeSet.add(v.make);
  });
  // Also include sold vehicle makes if we don't have many
  if (makeSet.size < 4) {
    vehicles.forEach((v) => {
      if (v.make) makeSet.add(v.make);
    });
  }

  const brands = Array.from(makeSet).sort();

  if (brands.length === 0) return null;

  return (
    <section className="bg-white px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-neutral-700">
          Shop By Brand
        </h2>
        <Link
          href="/shop"
          className="text-base md:text-xl text-neutral-600 hover:text-primary transition-colors"
        >
          See All
        </Link>
      </div>

      {/* Brand grid */}
      <div className="mt-8 md:mt-12 grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-x-4 md:gap-x-6 gap-y-6 md:gap-y-11">
        {brands.map((brand) => {
          const count = available.filter((v) => v.make === brand).length;
          const slug = brandSlug(brand);
          const hasLogo = BRAND_LOGOS.has(slug);
          return (
            <Link
              key={brand}
              href={`/shop?brand=${encodeURIComponent(brand)}`}
              className="flex flex-col items-center gap-2 md:gap-4 group"
            >
              {/* Brand badge (or the initial as a fallback) */}
              <div className="relative flex size-16 md:size-20 lg:size-[120px] items-center justify-center overflow-hidden rounded-full bg-white border border-neutral-50 group-hover:border-primary/30 transition-colors">
                {hasLogo ? (
                  <Image
                    src={`/images/brands/${slug}.png`}
                    alt={`${brand} logo`}
                    fill
                    sizes="120px"
                    className="object-contain p-2.5 md:p-3.5 lg:p-5"
                  />
                ) : (
                  <span className="text-lg md:text-xl lg:text-2xl font-semibold text-neutral-400 group-hover:text-primary transition-colors uppercase">
                    {brand.slice(0, 1)}
                  </span>
                )}
              </div>
              <div className="text-center">
                <span className="text-xs md:text-sm lg:text-base text-neutral-700 group-hover:text-primary transition-colors">
                  {brand}
                </span>
                {count > 0 && (
                  <p className="text-[10px] md:text-xs text-neutral-400">
                    {count} available
                  </p>
                )}
              </div>
            </Link>
          );
        })}
      </div>
    </section>
  );
}
