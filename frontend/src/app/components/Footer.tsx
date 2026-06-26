import Link from "next/link";
import Image from "next/image";
import { resolveNap } from "@/lib/nap";

const topBrands = ["Honda", "Toyota", "Chevrolet", "Ford", "Nissan", "Hyundai"];
const quickLinks = [
  { label: "Browse Inventory", href: "/shop" },
  { label: "About Us", href: "/about" },
  { label: "Financing", href: "/financing" },
  { label: "Contact Us", href: "/contact" },
  { label: "Blog", href: "/blog" },
];
const tags = [
  ["Under10k", "Under15k", "LowMileage", "CleanTitle"],
  ["FamilyCar", "FuelEfficient", "Sedan"],
  ["SUV", "Truck", "Compact"],
  ["FinancingAvailable", "GreatValue", "LowMiles"],
];

export default async function Footer() {
  const nap = await resolveNap();

  return (
    <footer className="bg-neutral-800">
      <div className="flex flex-col lg:flex-row gap-10 lg:gap-6 px-5 md:px-10 lg:px-20 py-10 md:py-14 lg:py-[72px]">
        {/* Brand info */}
        <div className="flex flex-col gap-6">
          <Image
            src="/images/brand/kelley-lockup.svg"
            alt={nap.name}
            width={200}
            height={36}
            unoptimized
            className="object-contain brightness-0 invert"
          />
          <p className="max-w-[248px] text-sm text-neutral-400">
            Reliable used vehicles · Simple, friendly vehicle shopping.
          </p>
          <div className="flex flex-col gap-3">
            <div>
              <p className="text-sm text-neutral-500">Contact Us:</p>
              {nap.telHref ? (
                <a
                  href={nap.telHref}
                  className="text-lg font-medium text-neutral-100 hover:text-primary transition-colors"
                >
                  {nap.phoneDisplay}
                </a>
              ) : (
                <span className="text-lg font-medium text-neutral-100">
                  {nap.phoneDisplay}
                </span>
              )}
            </div>
            {nap.hasAddress && (
              <p className="max-w-[248px] text-base text-neutral-300">
                {nap.addressLines.join(", ")}
              </p>
            )}
            {nap.email && (
              <a
                href={`mailto:${nap.email}`}
                className="text-base font-medium text-neutral-100 hover:text-primary transition-colors"
              >
                {nap.email}
              </a>
            )}
            {nap.hours && (
              <div>
                <p className="text-sm text-neutral-500">Hours:</p>
                <p className="max-w-[248px] text-sm text-neutral-300">
                  {nap.hoursText}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Navigation columns */}
        <div className="grid grid-cols-2 md:flex md:flex-1 gap-8 md:gap-6">
          {/* Top Brands */}
          <div>
            <h3 className="text-base font-medium text-neutral-100 md:w-[200px]">
              Popular Makes
            </h3>
            <ul className="mt-3 flex flex-col">
              {topBrands.map((brand) => (
                <li key={brand}>
                  <Link
                    href={`/shop?brand=${brand}`}
                    className="block py-1.5 text-sm font-medium text-neutral-400 hover:text-neutral-100 transition-colors"
                  >
                    {brand}
                  </Link>
                </li>
              ))}
              <li>
                <Link
                  href="/shop"
                  className="mt-1 flex items-center gap-2 py-1.5 text-sm font-medium text-primary hover:text-primary-dark transition-colors"
                >
                  Browse All Inventory
                  <svg className="size-4" fill="none" viewBox="0 0 20 20">
                    <path
                      d="M4.17 10h11.66M10 4.17 15.83 10 10 15.83"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </Link>
              </li>
            </ul>
          </div>

          {/* Quick Links */}
          <div>
            <h3 className="text-base font-medium text-neutral-100 md:w-[200px]">
              Quick Links
            </h3>
            <ul className="mt-3 flex flex-col">
              {quickLinks.map((link) => (
                <li key={link.label}>
                  <Link
                    href={link.href}
                    className="block py-1.5 text-sm font-medium text-neutral-400 hover:text-neutral-100 transition-colors"
                  >
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        </div>

        {/* Popular Tags */}
        <div>
          <h3 className="text-base font-medium text-neutral-100 lg:w-[312px]">
            Browse by Tag
          </h3>
          <div className="mt-4 md:mt-[18px] flex flex-wrap lg:flex-col gap-2">
            {tags.map((row, i) => (
              <div key={i} className="flex flex-wrap gap-2">
                {row.map((tag) => (
                  <Link
                    key={tag}
                    href={`/shop?tag=${tag}`}
                    className="rounded border border-neutral-600 px-3 py-1.5 text-sm font-medium text-neutral-100 hover:border-primary hover:text-primary transition-colors"
                  >
                    {tag}
                  </Link>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Copyright */}
      <div className="border-t border-[#303639] px-5 md:px-10 lg:px-20 py-6">
        <p className="text-center text-sm text-neutral-300">
          {nap.name} &nbsp;&copy; 2026 &nbsp;&mdash;&nbsp; Financing options available
        </p>
      </div>
    </footer>
  );
}
