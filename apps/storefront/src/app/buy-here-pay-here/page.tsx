import type { Metadata } from "next";
import InventoryPage from "../components/InventoryPage";
import { getVehicles } from "@/lib/api";
import { allInventoryMetaDescription } from "@/lib/inventory-seo";

export const revalidate = 60;

// The in-house-financed side of the lot: Kelley carries the note, so the
// number that matters to the shopper is the down payment, not the sticker.

export async function generateMetadata(): Promise<Metadata> {
  const { docs: vehicles } = await getVehicles({ limit: 100, saleType: "bhph" });
  return {
    title: "Buy Here Pay Here Cars in San Antonio, TX | Kelley Autoplex",
    description: allInventoryMetaDescription(
      vehicles,
      "Buy here, pay here cars in San Antonio, TX. In-house financing at Kelley Autoplex — no credit check, low money down. Browse current inventory."
    ),
    alternates: {
      canonical: "/buy-here-pay-here",
    },
  };
}

export default async function BuyHerePayHerePage() {
  return (
    <InventoryPage
      heading="Buy Here, Pay Here in San Antonio, TX"
      seoKind="all"
      saleType="bhph"
      saleTypeTabs="bhph"
      emptyText="Check back soon — new financed inventory is added regularly."
    />
  );
}
