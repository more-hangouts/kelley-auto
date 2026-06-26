import Image from "next/image";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Features from "../components/Features";
import Footer from "../components/Footer";
import ShopGrid from "./ShopGrid";
import { getHeroContent, getVehicles } from "@/lib/api";
import { isMediaDoc } from "@/types/cms";

export const revalidate = 60;

export default async function ShopPage() {
  const hero = await getHeroContent();
  const { docs: vehicles, totalDocs } = await getVehicles({ limit: 100 });

  const available = vehicles.filter((v) => v.status !== "SOLD");
  const bannerBgSrc =
    isMediaDoc(hero.bgImage) && hero.bgImage.url ? hero.bgImage.url : "/images/hero-bg.webp";

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Header */}
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
            Browse Inventory
          </h1>
          <p className="mt-2 md:mt-3 text-base md:text-lg text-neutral-100">
            {available.length > 0
              ? `${available.length} vehicle${available.length === 1 ? "" : "s"} available \u00b7 Cash only`
              : "Check back soon — new inventory added regularly."}
          </p>
        </div>
      </section>

      <ShopGrid vehicles={vehicles} totalDocs={totalDocs} />

      <Features />
      <Footer />
    </div>
  );
}
