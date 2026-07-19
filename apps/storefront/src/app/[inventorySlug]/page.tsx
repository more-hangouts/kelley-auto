import { notFound } from "next/navigation";
import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import {
  inventoryMetaDescription,
  matchInventoryLanding,
  makeFromSlug,
} from "@/lib/inventory-seo";

export const revalidate = 60;

function parseMakeSlug(slug: string): string | null {
  if (!slug.endsWith("-for-sale")) return null;
  const make = makeFromSlug(slug);
  return make ? make : null;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ inventorySlug: string }>;
}): Promise<Metadata> {
  const { inventorySlug } = await params;
  if (!inventorySlug.endsWith("-for-sale")) return {};

  const { docs: allVehicles } = await getVehicles({ limit: 500 });
  const match = matchInventoryLanding(inventorySlug, allVehicles);
  const label = match?.label || parseMakeSlug(inventorySlug) || makeFromSlug(inventorySlug);
  const vehicles = match?.vehicles ?? [];

  return {
    title: `${label} For Sale in San Antonio, TX | Kelley Autoplex`,
    description: inventoryMetaDescription(
      vehicles,
      label,
      `Browse used ${label} vehicles for sale at Kelley Autoplex in San Antonio, TX. View current inventory, photos, prices, and details.`
    ),
    alternates: {
      canonical: `/${inventorySlug}`,
    },
  };
}

export default async function MakeForSalePage({
  params,
}: {
  params: Promise<{ inventorySlug: string }>;
}) {
  const { inventorySlug } = await params;
  if (!inventorySlug.endsWith("-for-sale")) notFound();

  const { docs: allVehicles } = await getVehicles({ limit: 500 });
  const match = matchInventoryLanding(inventorySlug, allVehicles);
  const label = match?.label || parseMakeSlug(inventorySlug) || makeFromSlug(inventorySlug);

  return (
    <InventoryPage
      heading={`${label} For Sale in San Antonio, TX`}
      vehiclesOverride={match?.vehicles ?? []}
      seoLabel={label}
      seoFallback={`Browse used ${label} vehicles for sale at Kelley Autoplex in San Antonio, TX. Contact us to confirm availability.`}
      emptyText={`Check back soon — more ${label} vehicles are added regularly.`}
    />
  );
}
