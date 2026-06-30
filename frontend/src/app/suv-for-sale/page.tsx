import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { inventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({
    limit: 100,
    bodyTypes: ["SUV", "Sport Utility"],
  });
  return {
    title: "SUVs For Sale in San Antonio, TX | Kelley Autoplex",
    description: inventoryMetaDescription(
      vehicles,
      "SUV",
      "Browse used SUVs for sale at Kelley Autoplex in San Antonio, TX. View current SUV inventory, photos, prices, and details."
    ),
    alternates: {
      canonical: "/suv-for-sale",
    },
  };
}

export default async function SuvForSalePage() {
  return (
    <InventoryPage
      heading="SUVs For Sale in San Antonio, TX"
      bodyTypes={["SUV", "Sport Utility"]}
      seoLabel="SUV"
      emptyText="Check back soon — more SUVs are added regularly."
    />
  );
}
