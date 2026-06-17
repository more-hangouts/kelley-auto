"use client";

import { useState } from "react";
import VehicleCard from "../components/VehicleCard";
import type { PayloadVehicle } from "@/types/vehicle";

type PriceFilter = "all" | "under5k" | "5kto10k" | "10kto15k" | "over15k";

const FILTERS: { id: PriceFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "under5k", label: "Under $5k" },
  { id: "5kto10k", label: "$5k–$10k" },
  { id: "10kto15k", label: "$10k–$15k" },
  { id: "over15k", label: "$15k+" },
];

function matchesPrice(vehicle: PayloadVehicle, filter: PriceFilter): boolean {
  if (filter === "all") return true;
  const p = vehicle.cashPrice;
  if (p == null) return false;
  if (filter === "under5k") return p < 5000;
  if (filter === "5kto10k") return p >= 5000 && p < 10000;
  if (filter === "10kto15k") return p >= 10000 && p < 15000;
  if (filter === "over15k") return p >= 15000;
  return true;
}

export default function ShopGrid({
  vehicles,
  totalDocs,
}: {
  vehicles: PayloadVehicle[];
  totalDocs: number;
}) {
  const [activeFilter, setActiveFilter] = useState<PriceFilter>("all");

  const available = vehicles.filter((v) => v.status !== "SOLD");
  const sold = vehicles.filter((v) => v.status === "SOLD");
  const all = [...available, ...sold];
  const filtered = all.filter((v) => matchesPrice(v, activeFilter));

  return (
    <section className="px-5 md:px-10 lg:px-20 py-6 md:py-10">
      {/* Filter bar */}
      <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
        <div className="flex items-center gap-3 md:gap-4 w-full md:w-auto overflow-x-auto">
          <button className="flex-shrink-0 flex items-center gap-2 rounded-lg border border-neutral-50 bg-white px-4 py-3 text-sm font-medium text-neutral-700 shadow-sm hover:bg-neutral-25">
            <svg className="size-5" fill="none" viewBox="0 0 20 20">
              <path
                d="M2.5 5.83h15M5 10h10M7.5 14.17h5"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
            Filter
          </button>
          <div className="flex gap-2 text-sm">
            {FILTERS.map((f) => (
              <button
                key={f.id}
                onClick={() => setActiveFilter(f.id)}
                className={`flex-shrink-0 rounded-lg px-4 py-2 whitespace-nowrap transition-colors ${
                  activeFilter === f.id
                    ? "bg-gradient-to-b from-[#f9896a] to-primary text-white font-semibold shadow-sm"
                    : "bg-neutral-25 text-neutral-700 hover:bg-white"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2 text-sm text-neutral-600">
          <span>Sort by:</span>
          <select className="rounded-lg border border-neutral-50 bg-white px-3 py-2 text-sm font-medium text-neutral-700">
            <option>Newest First</option>
            <option>Price: Low to High</option>
            <option>Price: High to Low</option>
            <option>Lowest Miles</option>
          </select>
        </div>
      </div>

      {/* Vehicle grid */}
      {filtered.length > 0 ? (
        <div className="mt-6 md:mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {filtered.map((vehicle) => (
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
            {activeFilter === "all"
              ? "No vehicles listed yet."
              : "No vehicles in this price range."}
          </p>
          <p className="mt-2 text-sm text-neutral-400">
            {activeFilter === "all"
              ? "Check back soon, or contact us about what you're looking for."
              : "Try a different range or browse all inventory."}
          </p>
          {activeFilter !== "all" && (
            <button
              onClick={() => setActiveFilter("all")}
              className="mt-4 rounded-lg bg-primary/10 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/20 transition-colors"
            >
              Show All
            </button>
          )}
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
