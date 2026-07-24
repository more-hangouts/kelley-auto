"use client";

import { useState } from "react";
import Image from "next/image";

export default function ImageGallery({
  images,
  title,
}: {
  images: string[];
  title: string;
}) {
  const [active, setActive] = useState(0);

  const prev = () => setActive((a) => (a - 1 + images.length) % images.length);
  const next = () => setActive((a) => (a + 1) % images.length);

  return (
    <div className="flex flex-col gap-3">
      {/* Main image */}
      <div className="relative aspect-[4/3] overflow-hidden rounded-2xl bg-neutral-25">
        <Image
          src={images[active]}
          alt={title}
          fill
          priority
          className="object-contain p-6"
        />

        {images.length > 1 && (
          <>
            <button
              onClick={prev}
              aria-label="Previous image"
              className="absolute left-3 top-1/2 -translate-y-1/2 flex size-9 items-center justify-center rounded-full bg-white shadow-md hover:shadow-lg transition-shadow text-neutral-700 text-lg font-medium"
            >
              ‹
            </button>
            <button
              onClick={next}
              aria-label="Next image"
              className="absolute right-3 top-1/2 -translate-y-1/2 flex size-9 items-center justify-center rounded-full bg-white shadow-md hover:shadow-lg transition-shadow text-neutral-700 text-lg font-medium"
            >
              ›
            </button>
          </>
        )}

        {/* Photo count badge */}
        {images.length > 1 && (
          <div className="absolute bottom-3 right-3 flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1">
            <svg className="size-3.5 text-white" fill="none" viewBox="0 0 16 16">
              <rect x="2" y="3" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
              <circle cx="5.5" cy="6.5" r="1" stroke="currentColor" strokeWidth="1" />
              <path d="M2 11l3-3 2 2 3-3 4 4" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="text-[11px] font-medium text-white">
              {active + 1} / {images.length}
            </span>
          </div>
        )}
      </div>

      {/* Thumbnails */}
      {images.length > 1 && (
        <div className="flex gap-3 overflow-x-auto pb-1">
          {images.map((img, i) => (
            <button
              key={i}
              onClick={() => setActive(i)}
              className={`relative h-20 w-28 flex-shrink-0 overflow-hidden rounded-xl border-2 transition-colors ${
                i === active
                  ? "border-primary"
                  : "border-neutral-50 hover:border-neutral-200"
              }`}
            >
              <Image
                src={img}
                alt={`View ${i + 1}`}
                fill
                className="object-contain p-2"
              />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
