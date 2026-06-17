import { notFound } from "next/navigation";
import Link from "next/link";
import TopBanner from "@/app/components/TopBanner";
import NavbarWrapper from "@/app/components/NavbarWrapper";
import Features from "@/app/components/Features";
import Footer from "@/app/components/Footer";
import { getVehicle, displayYear, displayColor, isSold, lexicalToText } from "@/lib/api";
import ImageGallery from "./ImageGallery";
import InquiryForm from "./InquiryForm";
import type { PayloadVehicle } from "@/types/vehicle";

export const revalidate = 60;

export default async function CarDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const vehicle = await getVehicle(id);

  if (!vehicle) notFound();

  const title = `${displayYear(vehicle)} ${vehicle.make} ${vehicle.model}`;
  const sold = isSold(vehicle);
  const color = displayColor(vehicle);
  const description = lexicalToText(vehicle.description);

  // Collect all photo URLs for the gallery
  const photos = (vehicle.photos ?? []).map((p) => p.url);

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Breadcrumb */}
      <nav className="px-5 md:px-10 lg:px-20 py-4 text-sm text-neutral-400">
        <Link href="/" className="hover:text-neutral-600 transition-colors">
          Home
        </Link>
        <span className="mx-2">/</span>
        <Link href="/shop" className="hover:text-neutral-600 transition-colors">
          Inventory
        </Link>
        <span className="mx-2">/</span>
        <span className="text-neutral-600">{title}</span>
      </nav>

      {/* Main content */}
      <section className="px-5 md:px-10 lg:px-20 pb-16">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 lg:gap-12">
          {/* Left — image gallery */}
          <ImageGallery images={photos} title={title} />

          {/* Right — details */}
          <div className="flex flex-col gap-6">
            {/* Status badge */}
            <div className="flex items-center gap-3">
              <span
                className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold tracking-wide ${
                  sold
                    ? "bg-red-50 text-red-600"
                    : vehicle.status === "PENDING"
                    ? "bg-amber-50 text-amber-700"
                    : "bg-green-50 text-green-700"
                }`}
              >
                {sold ? "Sold" : vehicle.status === "PENDING" ? "Pending Sale" : "Available"}
              </span>
            </div>

            {/* Title */}
            <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700 leading-tight">
              {title}
            </h1>

            {/* Price */}
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-neutral-400">
                Cash Price
              </p>
              <p className="mt-1 text-4xl font-bold text-primary">
                {vehicle.cashPrice
                  ? `$${vehicle.cashPrice.toLocaleString()}`
                  : "Call for price"}
              </p>
            </div>

            {/* Quick specs */}
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-2xl border border-neutral-50 bg-neutral-25 p-5">
              {vehicle.mileage != null && (
                <div>
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">Mileage</p>
                  <p className="mt-0.5 text-sm font-semibold text-neutral-700">
                    {vehicle.mileage.toLocaleString()} mi
                  </p>
                </div>
              )}
              {color && (
                <div>
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">Exterior Color</p>
                  <p className="mt-0.5 text-sm font-semibold text-neutral-700">{color}</p>
                </div>
              )}
              {vehicle.transmission && (
                <div>
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">Transmission</p>
                  <p className="mt-0.5 text-sm font-semibold text-neutral-700">
                    {vehicle.transmission === "AUTOMATIC" ? "Automatic" : "Manual"}
                  </p>
                </div>
              )}
              {vehicle.fuelType && (
                <div>
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">Fuel Type</p>
                  <p className="mt-0.5 text-sm font-semibold text-neutral-700">
                    {vehicle.fuelType.charAt(0) + vehicle.fuelType.slice(1).toLowerCase()}
                  </p>
                </div>
              )}
              {vehicle.make && (
                <div>
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">Make</p>
                  <p className="mt-0.5 text-sm font-semibold text-neutral-700">{vehicle.make}</p>
                </div>
              )}
              <div>
                <p className="text-xs text-neutral-400 uppercase tracking-wide">Year</p>
                <p className="mt-0.5 text-sm font-semibold text-neutral-700">{vehicle.year}</p>
              </div>
              {vehicle.vin && (
                <div className="col-span-2">
                  <p className="text-xs text-neutral-400 uppercase tracking-wide">VIN</p>
                  <p className="mt-0.5 text-sm font-mono font-medium text-neutral-600">{vehicle.vin}</p>
                </div>
              )}
            </div>

            {/* Description */}
            {description && (
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-widest text-neutral-400 mb-2">
                  About This Car
                </h2>
                <p className="text-sm leading-relaxed text-neutral-500">{description}</p>
              </div>
            )}

            {/* Cash-only notice */}
            <div className="flex items-start gap-3 rounded-xl bg-primary/5 px-4 py-3">
              <svg className="mt-0.5 size-4 flex-shrink-0 text-primary" fill="none" viewBox="0 0 20 20">
                <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.4" />
                <path d="M10 9v5M10 7h.01" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
              </svg>
              <p className="text-xs text-neutral-600 leading-relaxed">
                <span className="font-semibold text-primary">Cash only</span> — No financing or online
                payments. All transactions completed in person at the lot.
              </p>
            </div>

            {/* CTA */}
            {!sold ? (
              <InquiryForm vehicleId={vehicle.id} vehicleTitle={title} />
            ) : (
              <div className="rounded-2xl border border-neutral-100 bg-neutral-25 p-6 text-center">
                <p className="font-medium text-neutral-500">This vehicle has been sold.</p>
                <Link
                  href="/shop"
                  className="mt-3 inline-block text-sm font-semibold text-primary hover:underline"
                >
                  View available cars →
                </Link>
              </div>
            )}
          </div>
        </div>

        {/* Description / specs tabs */}
        <div className="mt-14 border-t border-neutral-50 pt-10">
          <div className="flex gap-6 border-b border-neutral-50 mb-8">
            <span className="border-b-2 border-primary pb-3 text-sm font-semibold text-primary">
              Description
            </span>
            <span className="pb-3 text-sm font-medium text-neutral-400">Vehicle Details</span>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 lg:gap-16">
            <div>
              <h3 className="text-base font-semibold text-neutral-700 mb-3">About This Vehicle</h3>
              <p className="text-sm leading-relaxed text-neutral-500">
                {description || "No description provided."}
              </p>
            </div>

            <div>
              <h3 className="text-base font-semibold text-neutral-700 mb-3">
                Vehicle Specifications
              </h3>
              <table className="w-full text-sm">
                <tbody className="divide-y divide-neutral-50">
                  {buildSpecRows(vehicle, color).map(([label, value]) => (
                    <tr key={label}>
                      <td className="py-2.5 font-medium text-neutral-400 w-1/2">{label}</td>
                      <td className="py-2.5 font-semibold text-neutral-700">{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <Features />
      <Footer />
    </div>
  );
}

function buildSpecRows(v: PayloadVehicle, color: string): [string, string][] {
  const rows: [string, string][] = [];
  rows.push(["Year", v.year]);
  rows.push(["Make", v.make]);
  rows.push(["Model", v.model]);
  if (v.mileage != null) rows.push(["Mileage", `${v.mileage.toLocaleString()} mi`]);
  if (color) rows.push(["Exterior Color", color]);
  if (v.interiorColor) rows.push(["Interior Color", v.interiorColorCustom || v.interiorColor]);
  if (v.transmission) rows.push(["Transmission", v.transmission === "AUTOMATIC" ? "Automatic" : "Manual"]);
  if (v.fuelType) rows.push(["Fuel Type", v.fuelType.charAt(0) + v.fuelType.slice(1).toLowerCase()]);
  if (v.condition) rows.push(["Condition", v.condition === "NEW" ? "New" : "Used"]);
  if (v.vin) rows.push(["VIN", v.vin]);
  rows.push(["Status", v.status === "SOLD" ? "Sold" : v.status === "PENDING" ? "Pending Sale" : "Available"]);
  return rows;
}
