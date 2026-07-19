import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { inventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({ limit: 100, bodyType: "Sedan" });
  return {
    title: "Sedans For Sale in San Antonio, TX | Kelley Autoplex",
    description: inventoryMetaDescription(
      vehicles,
      "Sedan",
      "Browse used sedans for sale at Kelley Autoplex in San Antonio, TX. View current sedan inventory, photos, prices, and details."
    ),
    alternates: {
      canonical: "/sedan-for-sale",
    },
  };
}

export default async function SedanForSalePage() {
  return (
    <InventoryPage
      heading="Sedans For Sale in San Antonio, TX"
      bodyType="Sedan"
      seoLabel="Sedan"
      emptyText="Check back soon — more sedans are added regularly."
    />
  );
}
