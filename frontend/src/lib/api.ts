import { cache } from "react";
import type {
  PayloadVehicle,
  PayloadListResponse,
  MediaDoc,
} from "@/types/vehicle";
import type { SiteSettings, HeroContent, Post, Testimonial, ContactPage } from "@/types/cms";
import {
  getInventory as apiGetInventory,
  getVehicle as apiGetVehicle,
  type PublicVehicle,
} from "@/lib/publicApi";

// Re-export pure helpers so server components can still import from one place
export {
  lexicalToText,
  primaryPhoto,
  allPhotos,
  displayYear,
  displayColor,
  isSold,
} from "./vehicle-utils";

// ---------------------------------------------------------------------------
// Vehicle inventory — sourced from the FastAPI public API (Day 5 Slice 2).
//
// The site's components/helpers were written against the Payload `vehicles`
// shape, so the FastAPI `public_vehicle_dto` is adapted into `PayloadVehicle`
// here. This keeps every consumer (VehicleCard, ShopGrid, detail page,
// vehicle-utils) unchanged while the data now comes from the backend that
// owns inventory. Content (blog/testimonials/globals) still reads Payload
// via the functions further down.
// ---------------------------------------------------------------------------

// Build a minimal Lexical doc so lexicalToText() (and any richtext renderer)
// keep working — FastAPI carries no prose description, so we synthesize one
// from trim/body/features.
function toLexical(text: string): unknown {
  if (!text) return undefined;
  return {
    root: {
      type: "root",
      children: [
        { type: "paragraph", children: [{ type: "text", text }] },
      ],
    },
  };
}

function mapStatus(s: string | null): PayloadVehicle["status"] {
  switch ((s ?? "").toLowerCase()) {
    case "pending":
      return "PENDING";
    case "sold":
    case "delivered":
      return "SOLD";
    default:
      return "AVAILABLE";
  }
}

function mapTransmission(t: string | null): PayloadVehicle["transmission"] {
  const u = (t ?? "").toUpperCase();
  if (u.includes("AUTO")) return "AUTOMATIC";
  if (u.includes("MANUAL")) return "MANUAL";
  return null;
}

function mapFuel(f: string | null): PayloadVehicle["fuelType"] {
  const u = (f ?? "").toUpperCase();
  if (u.includes("DIESEL")) return "DIESEL";
  if (u.includes("ELECTRIC") || u === "EV") return "ELECTRIC";
  if (u.includes("HYBRID")) return "HYBRID";
  if (u.includes("GAS") || u.includes("PETROL")) return "GAS";
  return null;
}

function mapCondition(c: string | null): PayloadVehicle["condition"] {
  const u = (c ?? "").toLowerCase();
  if (u === "new") return "NEW";
  if (!u) return null;
  return "USED"; // used / certified / etc.
}

function toMedia(urls: string[]): MediaDoc[] {
  return urls.map((url, i) => ({
    id: String(i),
    url,
    filename: url.split("/").pop() || `photo-${i}`,
    mimeType: "image/*",
    alt: null,
  }));
}

function adaptVehicle(v: PublicVehicle): PayloadVehicle {
  const descParts = [v.trim, v.bodyType, (v.features ?? []).join(", ")].filter(
    Boolean
  ) as string[];
  return {
    id: String(v.id),
    title: v.title || [v.year, v.make, v.model].filter(Boolean).join(" "),
    vin: v.vin,
    make: v.make ?? "",
    model: v.model ?? "",
    year: v.year != null ? String(v.year) : "",
    cashPrice: v.priceCents != null ? Math.round(v.priceCents / 100) : null,
    mileage: v.mileage,
    condition: mapCondition(v.condition),
    exteriorColor: v.exteriorColor,
    exteriorColorCustom: null,
    interiorColor: v.interiorColor,
    interiorColorCustom: null,
    transmission: mapTransmission(v.transmission),
    fuelType: mapFuel(v.fuelType),
    description: toLexical(descParts.join(" · ")),
    status: mapStatus(v.status),
    photos: toMedia(v.photos ?? []),
    createdAt: v.createdAt,
    updatedAt: v.updatedAt,
  };
}

const _API_PAGE = 60; // FastAPI public list caps at 60/page

export const getVehicles = cache(
  async (opts: { limit?: number } = {}): Promise<PayloadListResponse<PayloadVehicle>> => {
    const target = opts.limit ?? 100;
    const docs: PayloadVehicle[] = [];
    let total = 0;
    let page = 1;
    // Fixed page size keeps the backend's offset math correct; accumulate
    // pages until we have `target` rows or the list is exhausted.
    while (docs.length < target) {
      const res = await apiGetInventory({
        limit: _API_PAGE,
        page,
        sort: "newest",
      });
      total = res.total;
      docs.push(...res.items.map(adaptVehicle));
      if (res.items.length < _API_PAGE || docs.length >= total) break;
      page += 1;
    }
    const limited = docs.slice(0, target);
    const totalDocs = total || limited.length;
    return {
      docs: limited,
      totalDocs,
      limit: target,
      totalPages: Math.max(1, Math.ceil(totalDocs / target)),
      page: 1,
      pagingCounter: 1,
      hasPrevPage: false,
      hasNextPage: totalDocs > limited.length,
      prevPage: null,
      nextPage: null,
    };
  }
);

export async function getVehicle(
  idOrListingCode: string
): Promise<PayloadVehicle | null> {
  const v = await apiGetVehicle(idOrListingCode);
  return v ? adaptVehicle(v) : null;
}

// ---------------------------------------------------------------------------
// Editorial content (site settings, hero, contact page, blog posts,
// testimonials) was formerly owned by Payload CMS. Payload was removed (its
// content was placeholder-only and its tables were never created), so these
// return static empties — every consumer already falls back to built-in
// defaults (hardcoded hero/colors, the Testimonials FALLBACK array, the
// blog "no posts yet" empty state). Re-point these at FastAPI
// `/api/public/*` if/when backend-owned content lands (see MIGRATION_PLAN
// blog endpoints). Kept as async with the same signatures so no caller
// changes.
// ---------------------------------------------------------------------------

export async function getSiteSettings(): Promise<SiteSettings> {
  return {};
}

export async function getHeroContent(): Promise<HeroContent> {
  return {};
}

export async function getContactPage(): Promise<ContactPage> {
  return {};
}

export async function getPosts(
  _opts: { limit?: number; publishedOnly?: boolean } = {}
): Promise<Post[]> {
  return [];
}

export async function getPost(_slug: string): Promise<Post | null> {
  return null;
}

export async function getTestimonials(): Promise<Testimonial[]> {
  return [];
}
