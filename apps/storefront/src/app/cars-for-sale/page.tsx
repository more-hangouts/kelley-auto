import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { allInventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({ limit: 100 });
  return {
    title: "Cars For Sale in San Antonio, TX | Kelley Autoplex",
    description: allInventoryMetaDescription(
      vehicles,
      "Shop current used cars for sale at Kelley Autoplex in San Antonio, TX. Browse inventory, pricing, photos, and vehicle details."
    ),
    alternates: {
      canonical: "/cars-for-sale",
    },
  };
}

export default async function CarsForSalePage() {
  return (
    <InventoryPage
      heading="Cars For Sale in San Antonio, TX"
      seoKind="all"
      saleTypeTabs="all"
    />
  );
}
