import type { PayloadVehicle } from "@/types/vehicle";

const MAKE_DISPLAY: Record<string, string> = {
  bmw: "BMW",
  gmc: "GMC",
  hummer: "HUMMER",
  infiniti: "INFINITI",
};

export function makeFromSlug(slug: string): string {
  const base = slug.replace(/-for-sale$/, "");
  const lower = base.toLowerCase();
  if (MAKE_DISPLAY[lower]) return MAKE_DISPLAY[lower];
  return base
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function slugFromMake(make: string): string {
  return `${make.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "")}-for-sale`;
}

export function slugBase(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function inventorySlug(value: string): string {
  return `${slugBase(value)}-for-sale`;
}

function pluralizeBodySlug(bodyType: string): string {
  const base = slugBase(bodyType);
  if (!base || base.endsWith("s")) return `${base}-for-sale`;
  if (base.endsWith("y")) return `${base.slice(0, -1)}ies-for-sale`;
  return `${base}s-for-sale`;
}

function same(a: string | null | undefined, b: string): boolean {
  return (a ?? "").toLowerCase() === b.toLowerCase();
}

export type InventoryLandingMatch = {
  label: string;
  kind: "make" | "body" | "model";
  vehicles: PayloadVehicle[];
};

export function matchInventoryLanding(
  slug: string,
  vehicles: PayloadVehicle[]
): InventoryLandingMatch | null {
  const normalized = slug.toLowerCase();
  const availableFirst = [...vehicles].sort((a, b) => {
    if (a.status === "SOLD" && b.status !== "SOLD") return 1;
    if (a.status !== "SOLD" && b.status === "SOLD") return -1;
    return 0;
  });

  for (const vehicle of availableFirst) {
    if (!vehicle.bodyType) continue;
    const bodyType = vehicle.bodyType;
    const bodySlugs = new Set([
      inventorySlug(bodyType),
      pluralizeBodySlug(bodyType),
    ]);
    if (bodySlugs.has(normalized)) {
      return {
        label: bodyType.endsWith("s") ? bodyType : `${bodyType}s`,
        kind: "body",
        vehicles: vehicles.filter((v) => same(v.bodyType, bodyType)),
      };
    }
  }

  for (const vehicle of availableFirst) {
    if (!vehicle.make) continue;
    const makeSlug = slugFromMake(vehicle.make);
    if (makeSlug === normalized) {
      return {
        label: vehicle.make,
        kind: "make",
        vehicles: vehicles.filter((v) => same(v.make, vehicle.make)),
      };
    }
  }

  for (const vehicle of availableFirst) {
    if (!vehicle.model) continue;
    const makeModel = [vehicle.make, vehicle.model].filter(Boolean).join(" ");
    const slugs = new Set([
      inventorySlug(makeModel),
      inventorySlug(vehicle.model),
    ]);
    if (slugs.has(normalized)) {
      return {
        label: makeModel || vehicle.model,
        kind: "model",
        vehicles: vehicles.filter(
          (v) =>
            same(v.model, vehicle.model) &&
            (!vehicle.make || same(v.make, vehicle.make))
        ),
      };
    }
  }

  return null;
}

function formatMoney(value: number): string {
  return `$${value.toLocaleString()}`;
}

function vehicleName(vehicle: PayloadVehicle): string {
  return [vehicle.make, vehicle.model].filter(Boolean).join(" ").trim();
}

export function inventoryMetaDescription(
  vehicles: PayloadVehicle[],
  label: string,
  fallback: string
): string {
  const available = vehicles.filter((vehicle) => vehicle.status !== "SOLD");
  if (available.length === 0) return fallback;

  const prices = available
    .map((vehicle) => vehicle.cashPrice)
    .filter((price): price is number => typeof price === "number");
  const range =
    prices.length > 0
      ? ` ranging between ${formatMoney(Math.min(...prices))} and ${formatMoney(Math.max(...prices))}`
      : "";
  const examples = Array.from(new Set(available.map(vehicleName).filter(Boolean))).slice(0, 3);
  const examplesText =
    examples.length > 0
      ? ` Shop for your ${examples.join(", ")} at Kelley Autoplex.`
      : " Shop current inventory and contact us to confirm availability.";

  return `Kelley Autoplex has ${available.length} ${label} listing${
    available.length === 1 ? "" : "s"
  }${range} for sale in San Antonio, TX.${examplesText}`;
}

export function allInventoryMetaDescription(
  vehicles: PayloadVehicle[],
  fallback: string
): string {
  const available = vehicles.filter((vehicle) => vehicle.status !== "SOLD");
  if (available.length === 0) return fallback;

  const counts = new Map<string, number>();
  for (const vehicle of available) {
    const key = vehicle.bodyType || "vehicle";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  const bodyText = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)
    .map(([bodyType, count]) => `${count} ${bodyType}`)
    .join(", ");

  const prices = available
    .map((vehicle) => vehicle.cashPrice)
    .filter((price): price is number => typeof price === "number");
  const range =
    prices.length > 0
      ? ` between ${formatMoney(Math.min(...prices))} and ${formatMoney(Math.max(...prices))}`
      : "";

  return `Kelley Autoplex has ${bodyText || `${available.length} vehicle`} listings for sale in San Antonio, TX${range}. Shop current inventory and contact us to confirm availability.`;
}
