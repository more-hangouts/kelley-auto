import Link from "next/link";
import type { Metadata } from "next";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import { getVehicles } from "@/lib/api";
import { inventorySlug, slugFromMake } from "@/lib/inventory-seo";
import { resolveNap } from "@/lib/nap";

export const revalidate = 60;

export const metadata: Metadata = {
  title: "Kelley Autoplex San Antonio TX | Sitemap",
  description:
    "Kelley Autoplex sitemap with links to inventory, loan application, contact information, body styles, makes, and vehicle model pages.",
  alternates: {
    canonical: "/sitemap",
  },
};

const pages = [
  { label: "Home", href: "/" },
  { label: "Cars For Sale", href: "/cars-for-sale" },
  { label: "Loan Application", href: "/loan-application" },
  { label: "Contact Us", href: "/contact-us" },
  { label: "Terms and Conditions", href: "/terms-and-conditions" },
];

function uniqueLinks(items: { label: string; href: string }[]) {
  const seen = new Set<string>();
  return items.filter((item) => {
    if (seen.has(item.href)) return false;
    seen.add(item.href);
    return true;
  });
}

export default async function HtmlSitemapPage() {
  const [nap, { docs: vehicles }] = await Promise.all([
    resolveNap(),
    getVehicles({ limit: 500 }),
  ]);

  const bodyStyles = uniqueLinks(
    vehicles
      .map((vehicle) => vehicle.bodyType)
      .filter((bodyType): bodyType is string => Boolean(bodyType))
      .sort()
      .map((bodyType) => ({
        label: `${bodyType}${bodyType.endsWith("s") ? "" : "s"} For Sale`,
        href: `/${inventorySlug(bodyType)}`,
      }))
  );

  const makes = uniqueLinks(
    vehicles
      .map((vehicle) => vehicle.make)
      .filter((make): make is string => Boolean(make))
      .sort()
      .map((make) => ({
        label: `${make} For Sale`,
        href: `/${slugFromMake(make)}`,
      }))
  );

  const models = uniqueLinks(
    vehicles
      .filter((vehicle) => vehicle.make && vehicle.model)
      .sort((a, b) =>
        `${a.make} ${a.model}`.localeCompare(`${b.make} ${b.model}`)
      )
      .map((vehicle) => {
        const label = `${vehicle.make} ${vehicle.model}`;
        return {
          label: `${label} For Sale`,
          href: `/${inventorySlug(label)}`,
        };
      })
  );

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      <main>
        <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-10 md:py-14">
          <p className="text-sm font-medium text-primary uppercase tracking-wide">
            {nap.name}
          </p>
          <h1 className="mt-2 text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-neutral-700">
            Sitemap
          </h1>
          <p className="mt-3 max-w-2xl text-base md:text-lg text-neutral-500">
            Find Kelley Autoplex pages, current inventory categories, and vehicle shopping links.
          </p>
        </section>

        <section className="px-5 md:px-10 lg:px-20 py-10 md:py-14 lg:py-16">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-10">
            <SitemapList title="Pages" links={pages} />
            <SitemapList title="Bodystyles" links={bodyStyles} />
            <SitemapList title="Makes" links={makes} />
            <SitemapList title="Models" links={models} />
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}

function SitemapList({
  title,
  links,
}: {
  title: string;
  links: { label: string; href: string }[];
}) {
  return (
    <section>
      <h2 className="text-xl font-semibold text-neutral-700">{title}</h2>
      {links.length > 0 ? (
        <ul className="mt-4 space-y-2">
          {links.map((link) => (
            <li key={link.href}>
              <Link
                href={link.href}
                className="text-sm font-medium text-neutral-500 hover:text-primary transition-colors"
              >
                {link.label}
              </Link>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 text-sm text-neutral-400">No links available yet.</p>
      )}
    </section>
  );
}
