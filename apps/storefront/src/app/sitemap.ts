import type { MetadataRoute } from "next";
import { getVehicles } from "@/lib/api";
import { inventorySlug, slugFromMake } from "@/lib/inventory-seo";
import { SITE_URL } from "@/lib/site";

const STATIC_ROUTES = [
  "",
  "/cars-for-sale",
  "/suv-for-sale",
  "/pickup-for-sale",
  "/sedan-for-sale",
  "/contact-us",
  "/about",
  "/loan-application",
  "/terms-and-conditions",
  "/sitemap",
];

const KNOWN_MAKE_ROUTES = [
  "/chevrolet-for-sale",
  "/dodge-for-sale",
  "/ford-for-sale",
  "/gmc-for-sale",
  "/hummer-for-sale",
  "/infiniti-for-sale",
  "/kia-for-sale",
  "/mazda-for-sale",
];

const KNOWN_INVENTORY_ROUTES = [
  "/wagons-for-sale",
  "/chevrolet-camaro-for-sale",
  "/infiniti-q70l-for-sale",
  "/kia-sportage-for-sale",
];

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();
  const { docs: vehicles } = await getVehicles({ limit: 500 });
  const makeRoutes = Array.from(
    new Set([
      ...KNOWN_MAKE_ROUTES,
      ...KNOWN_INVENTORY_ROUTES,
      ...vehicles
        .map((vehicle) => vehicle.make)
        .filter((make): make is string => Boolean(make))
        .map((make) => `/${slugFromMake(make)}`),
      ...vehicles
        .map((vehicle) => vehicle.bodyType)
        .filter((bodyType): bodyType is string => Boolean(bodyType))
        .map((bodyType) => `/${inventorySlug(bodyType)}`),
      ...vehicles
        .filter((vehicle) => vehicle.make && vehicle.model)
        .map((vehicle) => `/${inventorySlug(`${vehicle.make} ${vehicle.model}`)}`),
    ])
  );

  const staticUrls = [...STATIC_ROUTES, ...makeRoutes].map((route) => ({
    url: `${SITE_URL}${route}`,
    lastModified: now,
    changeFrequency: route.includes("for-sale") ? "daily" : "weekly",
    priority: route === "" ? 1 : route.includes("for-sale") ? 0.9 : 0.7,
  })) satisfies MetadataRoute.Sitemap;

  const vehicleUrls = vehicles.map((vehicle) => ({
    url: `${SITE_URL}/inventory/${vehicle.listingCode || vehicle.id}`,
    lastModified: vehicle.updatedAt ? new Date(vehicle.updatedAt) : now,
    changeFrequency: "daily",
    priority: 0.8,
  })) satisfies MetadataRoute.Sitemap;

  return [...staticUrls, ...vehicleUrls];
}
