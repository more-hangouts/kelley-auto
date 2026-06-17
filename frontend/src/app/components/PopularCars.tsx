"use client";

import { useState } from "react";
import Link from "next/link";
import type { PayloadVehicle } from "@/types/vehicle";
import VehicleCard from "./VehicleCard";

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

export default function PopularCars({ vehicles }: { vehicles: PayloadVehicle[] }) {
  const [activeFilter, setActiveFilter] = useState<PriceFilter>("all");

  const available = vehicles.filter((v) => v.status !== "SOLD");
  const filtered = available.filter((v) => matchesPrice(v, activeFilter));
  const preview = filtered.slice(0, 4);

  return (
    <section className="bg-white px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      {/* Title */}
      <div className="mx-auto max-w-[800px] text-center">
        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-neutral-700">
          Just Arrived
        </h2>
        <p className="mt-3 md:mt-4 text-base md:text-lg text-neutral-600">
          Fresh inventory added regularly. All vehicles inspected and priced to sell.
        </p>
      </div>

      {/* Price range tabs */}
      <div className="mx-auto mt-8 md:mt-10 flex max-w-[700px] overflow-x-auto gap-2 md:gap-3 rounded-xl bg-neutral-25 p-2">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setActiveFilter(f.id)}
            className={`flex-shrink-0 md:flex-1 rounded-lg px-4 py-2.5 text-sm whitespace-nowrap transition-colors ${
              activeFilter === f.id
                ? "bg-gradient-to-b from-[#f9896a] to-primary text-white font-semibold shadow-sm"
                : "text-neutral-700 hover:bg-white"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Car grid */}
      {preview.length > 0 ? (
        <div className="mt-8 md:mt-10 flex gap-6 overflow-x-auto snap-x snap-mandatory pb-4 md:grid md:grid-cols-2 lg:grid-cols-4 md:overflow-visible md:snap-none md:pb-0">
          {preview.map((vehicle) => (
            <div key={vehicle.id} className="min-w-[85%] snap-center md:min-w-0">
              <VehicleCard vehicle={vehicle} />
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-10 flex flex-col items-center justify-center py-16 text-center">
          <p className="text-neutral-400 text-base">
            {activeFilter === "all"
              ? "No vehicles in inventory yet."
              : "No vehicles in this price range."}
          </p>
          <p className="mt-1 text-sm text-neutral-400">
            {activeFilter === "all"
              ? "Check back soon — new cars added regularly."
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

      {/* CTA */}
      <div className="mt-8 md:mt-10 flex justify-center">
        <Link
          href="/shop"
          className="rounded-lg bg-primary px-7 py-3 text-base font-medium text-white shadow-sm hover:bg-primary-dark transition-colors"
        >
          View All Inventory
        </Link>
      </div>
    </section>
  );
}
