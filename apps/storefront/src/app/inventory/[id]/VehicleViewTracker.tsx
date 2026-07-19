"use client";

import { useEffect } from "react";
import { track } from "@/lib/analytics";
import { fbqTrack, vehicleContentParams } from "@/lib/metaPixel";

/**
 * Fires a first-party `vehicle_view` beacon once when a vehicle detail page
 * mounts, plus the Meta Pixel `ViewContent` twin (no-op when the Pixel isn't
 * configured) so Meta can build viewed-this-car retargeting audiences.
 * Renders nothing.
 */
export default function VehicleViewTracker({
  vehicleId,
  listingCode,
  year,
  make,
  model,
  priceCents,
}: {
  vehicleId: number | string;
  listingCode?: string | null;
  year?: string | number | null;
  make?: string | null;
  model?: string | null;
  priceCents?: number | null;
}) {
  useEffect(() => {
    const metadata: Record<string, unknown> = {};
    if (year) metadata.vehicle_year = String(year);
    if (make) metadata.vehicle_make = make;
    if (model) metadata.vehicle_model = model;
    if (typeof priceCents === "number") metadata.price_cents = priceCents;
    track("vehicle_view", {
      vehicle: { vehicleId, listingCode: listingCode ?? undefined },
      metadata,
    });
    fbqTrack(
      "ViewContent",
      vehicleContentParams({ listingCode, vehicleId, year, make, model, priceCents })
    );
  }, [vehicleId, listingCode, year, make, model, priceCents]);

  return null;
}
