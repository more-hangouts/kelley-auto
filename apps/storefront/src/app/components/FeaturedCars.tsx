import type { PayloadVehicle } from "@/types/vehicle";
import VehicleCard from "./VehicleCard";

export default function FeaturedCars({ vehicles }: { vehicles: PayloadVehicle[] }) {
  const featured = vehicles.slice(4, 8);

  return (
    <section className="bg-white px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      <div className="mx-auto max-w-[800px] text-center">
        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-neutral-700">
          More to Explore
        </h2>
        <p className="mt-3 md:mt-4 text-base md:text-lg text-neutral-600">
          Quality used cars at honest prices. Every vehicle inspected and ready to drive.
        </p>
      </div>

      {featured.length > 0 ? (
        <div className="mt-8 md:mt-14 flex gap-6 overflow-x-auto snap-x snap-mandatory pb-4 md:grid md:grid-cols-2 lg:grid-cols-4 md:overflow-visible md:snap-none md:pb-0">
          {featured.map((vehicle) => (
            <div key={vehicle.id} className="min-w-[85%] snap-center md:min-w-0">
              <VehicleCard vehicle={vehicle} />
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
