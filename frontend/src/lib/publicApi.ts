// Typed client for the Kelley FastAPI public API (`/api/public/*`).
//
// Day 5: the public site reads vehicle inventory + business profile from,
// and posts leads to, the FastAPI backend (not Payload/Prisma). Content
// (blog/pages) still comes from Payload via src/lib/api.ts.
//
// Base URL: server components/route handlers prefer API_BASE_URL (an
// internal address that need not be public); the browser uses
// NEXT_PUBLIC_API_BASE_URL. Both default to local dev. The public
// endpoints are unauthenticated and CORS-safe, so the browser can call
// them directly — no Next relay needed.

const SERVER_BASE =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://127.0.0.1:8000";

const BROWSER_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

function baseUrl(): string {
  return typeof window === "undefined" ? SERVER_BASE : BROWSER_BASE;
}

// ---------------------------------------------------------------------------
// Types — mirror services/catalog_service.public_vehicle_dto (camelCase)
// ---------------------------------------------------------------------------

export interface PublicVehicle {
  id: number;
  listingCode: string;
  title: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  trim: string | null;
  priceCents: number | null;
  mileage: number | null;
  status: string | null;
  condition: string | null;
  exteriorColor: string | null;
  interiorColor: string | null;
  transmission: string | null;
  fuelType: string | null;
  bodyType: string | null;
  drivetrain: string | null;
  vin: string | null;
  photos: string[];
  features: string[];
  carfaxUrl: string | null;
  videoUrl: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface InventoryPage {
  items: PublicVehicle[];
  total: number;
  page: number;
  limit: number;
}

export interface InventoryQuery {
  make?: string;
  model?: string;
  bodyType?: string;
  fuelType?: string;
  transmission?: string;
  drivetrain?: string;
  minPrice?: number; // whole USD
  maxPrice?: number; // whole USD
  minYear?: number;
  maxYear?: number;
  maxMileage?: number;
  q?: string;
  status?: "available" | "pending";
  sort?:
    | "newest"
    | "oldest"
    | "price_asc"
    | "price_desc"
    | "year_desc"
    | "year_asc"
    | "mileage_asc";
  page?: number;
  limit?: number;
}

export interface BusinessHoursDay {
  day: string;
  closed?: boolean;
  open?: string;
  close?: string;
}

export interface BusinessHours {
  timezone?: string | null;
  days?: BusinessHoursDay[];
}

export interface PublicBusinessProfile {
  name: string | null;
  legalName: string;
  address: {
    line1: string | null;
    line2: string | null;
    city: string | null;
    state: string | null;
    postalCode: string | null;
    country: string | null;
  };
  phone: string | null;
  email: string | null;
  website: string | null;
  hours?: BusinessHours | null;
}

export interface LeadInput {
  name?: string;
  phone?: string;
  email?: string;
  // Either is accepted; a numeric id links by id, anything else by code.
  vehicleId?: number | string | null;
  listingCode?: string | null;
  message?: string;
  preferredDay?: string;
  preferredTime?: string;
  sourcePage?: string;
  utm?: Partial<
    Record<"source" | "medium" | "campaign" | "term" | "content", string>
  >;
}

export interface LeadResult {
  ok: boolean;
  message: string;
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

const QUERY_KEYS: Record<keyof InventoryQuery, string> = {
  make: "make",
  model: "model",
  bodyType: "body_type",
  fuelType: "fuel_type",
  transmission: "transmission",
  drivetrain: "drivetrain",
  minPrice: "min_price",
  maxPrice: "max_price",
  minYear: "min_year",
  maxYear: "max_year",
  maxMileage: "max_mileage",
  q: "q",
  status: "status",
  sort: "sort",
  page: "page",
  limit: "limit",
};

function buildQuery(query: InventoryQuery): string {
  const params = new URLSearchParams();
  (Object.keys(query) as (keyof InventoryQuery)[]).forEach((key) => {
    const value = query[key];
    if (value !== undefined && value !== null && value !== "") {
      params.set(QUERY_KEYS[key], String(value));
    }
  });
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function getInventory(
  query: InventoryQuery = {}
): Promise<InventoryPage> {
  const res = await fetch(
    `${baseUrl()}/api/public/inventory${buildQuery(query)}`,
    { next: { revalidate: 60 } }
  );
  if (!res.ok) {
    return { items: [], total: 0, page: query.page ?? 1, limit: query.limit ?? 24 };
  }
  return (await res.json()) as InventoryPage;
}

export async function getVehicle(
  idOrListingCode: string | number
): Promise<PublicVehicle | null> {
  const res = await fetch(
    `${baseUrl()}/api/public/inventory/${encodeURIComponent(
      String(idOrListingCode)
    )}`,
    { next: { revalidate: 60 } }
  );
  if (res.status === 404) return null;
  if (!res.ok) return null;
  return (await res.json()) as PublicVehicle;
}

export async function getBusinessProfile(): Promise<PublicBusinessProfile | null> {
  const res = await fetch(`${baseUrl()}/api/public/business-profile`, {
    next: { revalidate: 300 },
  });
  if (!res.ok) return null;
  return (await res.json()) as PublicBusinessProfile;
}

// ---------------------------------------------------------------------------
// Writes
// ---------------------------------------------------------------------------

export async function submitLead(input: LeadInput): Promise<LeadResult> {
  const body: Record<string, unknown> = {};
  if (input.name) body.name = input.name;
  if (input.phone) body.phone = input.phone;
  if (input.email) body.email = input.email;
  if (input.message) body.message = input.message;
  if (input.preferredDay) body.preferred_day = input.preferredDay;
  if (input.preferredTime) body.preferred_time = input.preferredTime;
  if (input.sourcePage) body.source_page = input.sourcePage;

  // Vehicle ref: explicit listingCode wins; otherwise a numeric id links by
  // id and any other token links by code. A ref the backend can't resolve
  // degrades to a general lead server-side — never an error here.
  if (input.listingCode) {
    body.listing_code = input.listingCode;
  } else if (input.vehicleId !== undefined && input.vehicleId !== null) {
    const token = String(input.vehicleId).trim();
    if (/^\d+$/.test(token)) body.vehicle_id = Number(token);
    else if (token) body.listing_code = token;
  }

  if (input.utm) {
    if (input.utm.source) body.utm_source = input.utm.source;
    if (input.utm.medium) body.utm_medium = input.utm.medium;
    if (input.utm.campaign) body.utm_campaign = input.utm.campaign;
    if (input.utm.term) body.utm_term = input.utm.term;
    if (input.utm.content) body.utm_content = input.utm.content;
  }

  try {
    const res = await fetch(`${baseUrl()}/api/public/leads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      return { ok: false, message: "We couldn't submit your request." };
    }
    return (await res.json()) as LeadResult;
  } catch {
    return { ok: false, message: "Could not connect. Please call us." };
  }
}
