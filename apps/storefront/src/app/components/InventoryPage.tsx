import Image from "next/image";
import TopBanner from "./TopBanner";
import NavbarWrapper from "./NavbarWrapper";
import Features from "./Features";
import Footer from "./Footer";
import ShopGrid from "../shop/ShopGrid";
import { getHeroContent, getVehicles } from "@/lib/api";
import {
  allInventoryMetaDescription,
  inventoryMetaDescription,
} from "@/lib/inventory-seo";
import { isMediaDoc } from "@/types/cms";
import type { PayloadVehicle } from "@/types/vehicle";

export type InventoryPageConfig = {
  heading: string;
  emptyText?: string;
  bodyType?: string;
  bodyTypes?: string[];
  make?: string;
  seoLabel?: string;
  seoFallback?: string;
  seoKind?: "all" | "filtered";
  vehiclesOverride?: PayloadVehicle[];
};

export default async function InventoryPage({
  heading,
  emptyText = "Check back soon — new inventory added regularly.",
  bodyType,
  bodyTypes,
  make,
  seoLabel = "vehicle",
  seoFallback = "Browse current Kelley Autoplex inventory in San Antonio, TX. Contact us to confirm availability.",
  seoKind = "filtered",
  vehiclesOverride,
}: InventoryPageConfig) {
  const hero = await getHeroContent();
  const inventory = vehiclesOverride
    ? { docs: vehiclesOverride, totalDocs: vehiclesOverride.length }
    : await getVehicles({
        limit: 100,
        bodyType,
        bodyTypes,
        make,
      });
  const { docs: vehicles, totalDocs } = inventory;

  const available = vehicles.filter((v) => v.status !== "SOLD");
  const bannerBgSrc =
    isMediaDoc(hero.bgImage) && hero.bgImage.url
      ? hero.bgImage.url
      : "/images/hero-bg.webp";
  const seoText =
    seoKind === "all"
      ? allInventoryMetaDescription(vehicles, seoFallback)
      : inventoryMetaDescription(vehicles, seoLabel, seoFallback);

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      <section className="relative overflow-hidden px-5 md:px-10 lg:px-20 py-12 md:py-16 lg:py-20">
        <Image
          src={bannerBgSrc}
          alt="Inventory banner background"
          fill
          className="object-cover"
          priority
        />
        <div className="absolute inset-0 bg-black/45" />
        <div className="absolute inset-0 bg-gradient-to-r from-black/65 via-black/45 to-black/30" />

        <div className="relative z-10">
          <h1 className="text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-white">
            {heading}
          </h1>
          <p className="mt-2 md:mt-3 text-base md:text-lg text-neutral-100">
            {available.length > 0
              ? `${available.length} vehicle${available.length === 1 ? "" : "s"} available · Buy here, pay here`
              : emptyText}
          </p>
        </div>
      </section>

      <ShopGrid vehicles={vehicles} totalDocs={totalDocs} />

      <section className="px-5 md:px-10 lg:px-20 pb-10">
        <p className="max-w-4xl text-sm leading-6 text-neutral-500">
          {seoText}
        </p>
      </section>

      <Features />
      <Footer />
    </div>
  );
}
