"use client";

import Link from "next/link";
import type { PayloadVehicle } from "@/types/vehicle";
import VehicleCard from "./VehicleCard";

export default function PopularCars({ vehicles }: { vehicles: PayloadVehicle[] }) {
  const available = vehicles.filter((v) => v.status !== "SOLD");
  const preview = available.slice(0, 4);

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
            No vehicles in inventory yet.
          </p>
          <p className="mt-1 text-sm text-neutral-400">
            Check back soon — new cars added regularly.
          </p>
        </div>
      )}

      {/* CTA */}
      <div className="mt-8 md:mt-10 flex justify-center">
        <Link
          href="/cars-for-sale"
          className="rounded-lg bg-primary px-7 py-3 text-base font-medium text-white shadow-sm hover:bg-primary-dark transition-colors"
        >
          View All Inventory
        </Link>
      </div>
    </section>
  );
}
