import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";

export const revalidate = 60;

export const metadata: Metadata = {
  title: "Cars For Sale in San Antonio, TX | Kelley Autoplex",
  description:
    "Browse current Kelley Autoplex inventory in San Antonio, TX. View used cars, SUVs, sedans, and pickups available by appointment.",
  alternates: {
    canonical: "/cars-for-sale",
  },
};

export default async function ShopPage() {
  return <InventoryPage heading="Browse Inventory" saleTypeTabs="all" />;
}
