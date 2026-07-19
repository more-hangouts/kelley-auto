import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { inventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({
    limit: 100,
    bodyTypes: ["Pickup", "Truck"],
  });
  return {
    title: "Pickups For Sale in San Antonio, TX | Kelley Autoplex",
    description: inventoryMetaDescription(
      vehicles,
      "Pickup",
      "Browse used pickup trucks for sale at Kelley Autoplex in San Antonio, TX. View current truck inventory, photos, prices, and details."
    ),
    alternates: {
      canonical: "/pickup-for-sale",
    },
  };
}

export default async function PickupForSalePage() {
  return (
    <InventoryPage
      heading="Pickups For Sale in San Antonio, TX"
      bodyTypes={["Pickup", "Truck"]}
      seoLabel="Pickup"
      emptyText="Check back soon — more pickups are added regularly."
    />
  );
}
