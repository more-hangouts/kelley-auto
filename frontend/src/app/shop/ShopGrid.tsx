"use client";

import VehicleCard from "../components/VehicleCard";
import type { PayloadVehicle } from "@/types/vehicle";

export default function ShopGrid({
  vehicles,
  totalDocs,
}: {
  vehicles: PayloadVehicle[];
  totalDocs: number;
}) {
  const available = vehicles.filter((v) => v.status !== "SOLD");
  const sold = vehicles.filter((v) => v.status === "SOLD");
  const all = [...available, ...sold];

  return (
    <section className="px-5 md:px-10 lg:px-20 py-6 md:py-10">
      {/* Vehicle grid */}
      {all.length > 0 ? (
        <div className="mt-6 md:mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {all.map((vehicle) => (
            <VehicleCard key={vehicle.id} vehicle={vehicle} />
          ))}
        </div>
      ) : (
        <div className="mt-16 flex flex-col items-center justify-center py-20 text-center">
          <svg
            className="size-16 text-neutral-200 mb-4"
            fill="none"
            viewBox="0 0 64 64"
          >
            <rect
              x="8"
              y="18"
              width="40"
              height="28"
              rx="4"
              stroke="currentColor"
              strokeWidth="2"
            />
            <path
              d="M48 32h8l4 8v6H48v-14Z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinejoin="round"
            />
            <circle cx="18" cy="48" r="5" stroke="currentColor" strokeWidth="2" />
            <circle cx="42" cy="48" r="5" stroke="currentColor" strokeWidth="2" />
          </svg>
          <p className="text-lg font-medium text-neutral-500">
            No vehicles listed yet.
          </p>
          <p className="mt-2 text-sm text-neutral-400">
            Check back soon, or contact us about what you're looking for.
          </p>
        </div>
      )}

      {/* Pagination — only show if there's enough content */}
      {totalDocs > 20 && (
        <div className="mt-10 md:mt-12 flex items-center justify-center gap-4">
          <button className="flex size-10 items-center justify-center rounded-lg border border-neutral-50 bg-white shadow-sm hover:bg-neutral-25">
            <svg className="size-5" fill="none" viewBox="0 0 20 20">
              <path
                d="M12.5 15 7.5 10l5-5"
                stroke="#272835"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
          <button className="flex size-10 items-center justify-center rounded-lg bg-primary text-sm font-medium text-white">
            1
          </button>
          <button className="flex size-10 items-center justify-center rounded-lg border border-neutral-50 bg-white shadow-sm hover:bg-neutral-25">
            <svg className="size-5" fill="none" viewBox="0 0 20 20">
              <path
                d="M7.5 15 12.5 10l-5-5"
                stroke="#272835"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      )}
    </section>
  );
}
