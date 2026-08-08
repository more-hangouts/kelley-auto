import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { allInventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

// Cars sold outright, no dealer financing. A real route rather than
// client-side tab state so a rep can text the link directly and so the
// page can carry its own title and description into search.

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({ limit: 100, saleType: "cash" });
  return {
    title: "Cash Cars For Sale in San Antonio, TX | Kelley Autoplex",
    description: allInventoryMetaDescription(
      vehicles,
      "Shop cash cars for sale at Kelley Autoplex in San Antonio, TX — vehicles sold outright, no financing. Browse inventory, pricing, and photos."
    ),
    alternates: {
      canonical: "/cash-cars",
    },
  };
}

export default async function CashCarsPage() {
  return (
    <InventoryPage
      heading="Cash Cars in San Antonio, TX"
      seoKind="all"
      saleType="cash"
      saleTypeTabs="cash"
      emptyText="No cash cars in stock right now — check back soon, or browse the full inventory."
    />
  );
}
