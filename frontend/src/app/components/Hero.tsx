import Image from "next/image";
import Link from "next/link";
import NavbarWrapper from "./NavbarWrapper";
import { getHeroContent } from "@/lib/api";
import { isMediaDoc } from "@/types/cms";

export default async function Hero() {
  const hero = await getHeroContent();

  const watermark = hero.watermark || "Buy Here Pay Here";
  const subheadline =
    hero.subheadline ||
    "Choose from quality pre-owned vehicles you can trust. Get approved in minutes with buy here, pay here options.";
  const ctaHref = "/loan-application#application";
  const headline = hero.headline || "Find the perfect car that fits your journey";
  const bgSrc = isMediaDoc(hero.bgImage) && hero.bgImage.url ? hero.bgImage.url : "/images/hero-bg.webp";
  const showCarImage = hero.showCarImage !== false;
  const carSrc = isMediaDoc(hero.carImage) && hero.carImage.url ? hero.carImage.url : null;

  return (
    <section className="relative h-[500px] md:h-[700px] lg:h-[940px] overflow-hidden">
      {/* Background image */}
      <Image
        src={bgSrc}
        alt="Hero background"
        fill
        className="object-cover"
        priority
      />

      {/* Gradient overlays */}
      <div className="absolute inset-x-0 bottom-0 h-64 bg-gradient-to-t from-black/30 to-transparent" />
      <div className="absolute inset-x-0 top-0 h-64 bg-gradient-to-b from-black/30 to-transparent" />

      {/* Watermark text — fluid size so it fills the hero width on one line */}
      <div className="absolute inset-0 flex items-center justify-center px-4">
        <h1 className="font-[family-name:var(--font-bebas)] text-[clamp(2.5rem,12.5vw,17rem)] leading-none tracking-[2px] whitespace-nowrap bg-gradient-to-b from-white/90 to-white/25 bg-clip-text text-transparent select-none uppercase text-center [-webkit-text-stroke:1px_rgba(255,255,255,0.4)] drop-shadow-[0_6px_16px_rgba(0,0,0,0.45)]">
          {watermark}
        </h1>
      </div>

      {/* Car image */}
      {showCarImage && carSrc && (
        <div className="absolute inset-0 z-10 flex items-center justify-center px-5 md:px-10 lg:px-20">
          <div className="relative w-full h-[60%] mt-10">
            <Image
              src={carSrc}
              alt="Featured car"
              fill
              className="object-contain"
              priority
            />
          </div>
        </div>
      )}

      {/* Navbar — z-50 so the open mobile menu overlays the bottom CTA
          (a same-z-20 sibling that otherwise paints over the dropdown). */}
      <div className="absolute inset-x-0 top-0 z-50">
        <NavbarWrapper light />
      </div>

      {/* Bottom CTA */}
      <div className="absolute inset-x-0 bottom-0 z-20 flex flex-col lg:flex-row items-start lg:items-end justify-between px-5 md:px-10 lg:px-20 pb-6 md:pb-8 lg:pb-12 gap-4 lg:gap-0">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-4 md:gap-5">
          <p className="max-w-[414px] text-sm md:text-base lg:text-xl leading-5 md:leading-7 lg:leading-[30px] text-white">
            {subheadline}
          </p>
          <Link
            href={ctaHref}
            className="flex items-center gap-3 rounded-lg bg-white px-5 py-3 text-neutral-700 shadow-sm hover:bg-neutral-50 transition-colors"
          >
            <span className="flex flex-col leading-tight">
              <span className="text-sm md:text-base font-semibold uppercase tracking-wide">
                Get Approved
              </span>
              <span className="text-xs md:text-sm font-medium text-primary">
                No Credit Check
              </span>
            </span>
            <svg className="size-4" fill="none" viewBox="0 0 16 16">
              <path
                d="M3.33 8h9.34M8.67 4l4 4-4 4"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </Link>
        </div>
        <p className="max-w-[300px] lg:max-w-[466px] font-[family-name:var(--font-bebas)] text-3xl md:text-5xl lg:text-[64px] leading-8 md:leading-[50px] lg:leading-[64px] text-white lg:text-right">
          {headline}
        </p>
      </div>
    </section>
  );
}
