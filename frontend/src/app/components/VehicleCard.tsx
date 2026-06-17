import Image from "next/image";
import Link from "next/link";
import type { PayloadVehicle } from "@/types/vehicle";
import { primaryPhoto, displayYear, displayColor, isSold, lexicalToText, allPhotos } from "@/lib/vehicle-utils";

export default function VehicleCard({ vehicle }: { vehicle: PayloadVehicle }) {
  const imageUrl = primaryPhoto(vehicle);
  const photoCount = allPhotos(vehicle).length;
  const year = displayYear(vehicle);
  const color = displayColor(vehicle);
  const sold = isSold(vehicle);
  const description = lexicalToText(vehicle.description);

  return (
    <Link
      href={`/inventory/${vehicle.id}`}
      className="group flex flex-1 flex-col overflow-hidden rounded-2xl border border-neutral-50 hover:shadow-lg transition-shadow"
    >
      {/* Image */}
      <div className="relative h-[180px] md:h-[220px] bg-neutral-25">
        {imageUrl ? (
          <Image
            src={imageUrl}
            alt={vehicle.title}
            fill
            className="object-contain p-4 md:p-6"
          />
        ) : (
          <div className="flex h-full items-center justify-center">
            <svg className="size-16 text-neutral-200" fill="none" viewBox="0 0 64 64">
              <rect x="8" y="18" width="40" height="28" rx="4" stroke="currentColor" strokeWidth="2" />
              <path d="M48 32h8l4 8v6H48v-14Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
              <circle cx="18" cy="48" r="5" stroke="currentColor" strokeWidth="2" />
              <circle cx="42" cy="48" r="5" stroke="currentColor" strokeWidth="2" />
            </svg>
          </div>
        )}

        {/* Photo count badge */}
        {photoCount > 1 && (
          <div className="absolute right-3 bottom-3 flex items-center gap-1 rounded-full bg-black/60 px-2.5 py-1">
            <svg className="size-3.5 text-white" fill="none" viewBox="0 0 16 16">
              <rect x="2" y="3" width="12" height="10" rx="1.5" stroke="currentColor" strokeWidth="1.2" />
              <circle cx="5.5" cy="6.5" r="1" stroke="currentColor" strokeWidth="1" />
              <path d="M2 11l3-3 2 2 3-3 4 4" stroke="currentColor" strokeWidth="1" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <span className="text-[11px] font-medium text-white">{photoCount}</span>
          </div>
        )}

        {/* Pending badge */}
        {vehicle.status === "PENDING" && !sold && (
          <div className="absolute left-3 top-3">
            <span className="rounded-full bg-amber-500 px-3 py-1 text-xs font-semibold text-white uppercase tracking-wide">
              Pending
            </span>
          </div>
        )}

        {/* Sold overlay */}
        {sold && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/40">
            <span className="rounded-full bg-red-500 px-4 py-1.5 text-sm font-semibold text-white uppercase tracking-wide">
              Sold
            </span>
          </div>
        )}
      </div>

      {/* Details */}
      <div className="flex flex-1 flex-col p-4 md:p-5">
        <h3 className="text-base font-semibold text-neutral-700">
          {year} {vehicle.make} {vehicle.model}
        </h3>

        <p className="mt-1 text-lg md:text-xl font-semibold text-primary">
          {vehicle.cashPrice
            ? `$${vehicle.cashPrice.toLocaleString()}`
            : "Call for price"}
        </p>

        {description && (
          <p className="mt-2 text-sm text-neutral-500 line-clamp-2 leading-5">
            {description}
          </p>
        )}

        {/* Specs bar */}
        <div className="mt-auto pt-4 flex items-center gap-3 md:gap-4 border-t border-neutral-50 text-xs md:text-sm text-neutral-500">
          {vehicle.mileage != null && (
            <>
              <div className="flex items-center gap-1.5">
                <svg className="size-4 text-neutral-400" fill="none" viewBox="0 0 16 16">
                  <circle cx="8" cy="8" r="5.5" stroke="currentColor" strokeWidth="1.2" />
                  <path d="M8 5v3l2 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                {vehicle.mileage.toLocaleString()} mi
              </div>
              {color && <div className="h-3 w-px bg-neutral-100" />}
            </>
          )}
          {color && (
            <div className="flex items-center gap-1.5">
              <div
                className="size-3 rounded-full border border-neutral-200"
                style={{ backgroundColor: colorToHex(color) }}
              />
              {color}
            </div>
          )}
        </div>
      </div>
    </Link>
  );
}

function colorToHex(color: string): string {
  const map: Record<string, string> = {
    Black: "#1a1a1a",
    White: "#f5f5f5",
    Silver: "#c0c0c0",
    Gray: "#808080",
    "Dark Gray": "#4b4b4b",
    Red: "#dc2626",
    Blue: "#2563eb",
    "Navy Blue": "#1e3a5f",
    "Dark Blue": "#1e3a5f",
    Green: "#16a34a",
    Brown: "#92400e",
    Gold: "#ca8a04",
    Orange: "#ea580c",
    Beige: "#d4c5a9",
    Maroon: "#7f1d1d",
    Champagne: "#f7e7ce",
    "Pearl White": "#f0ede8",
    Gunmetal: "#2c3e50",
    Purple: "#7e22ce",
    Yellow: "#eab308",
    Tan: "#d2b48c",
    Cream: "#fffdd0",
  };
  return map[color] || "#d4d4d4";
}
