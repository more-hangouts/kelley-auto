import { cache } from "react";
import { unstable_cache } from "next/cache";
import { getPayload } from "payload";
import configPromise from "@payload-config";
import type { PayloadVehicle, PayloadListResponse } from "@/types/vehicle";
import type { SiteSettings, HeroContent, Post, Testimonial, ContactPage } from "@/types/cms";

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
// Payload local API — no HTTP round trips since we're inside the same app
// ---------------------------------------------------------------------------

export const getVehicles = cache(
  async (opts: { limit?: number } = {}): Promise<PayloadListResponse<PayloadVehicle>> => {
    try {
      const payload = await getPayload({ config: configPromise });
      const result = await payload.find({
        collection: "vehicles",
        limit: opts.limit ?? 100,
        sort: "-createdAt",
        depth: 1,
      });
      return result as unknown as PayloadListResponse<PayloadVehicle>;
    } catch (err) {
      console.error("getVehicles error:", err);
      return {
        docs: [],
        totalDocs: 0,
        limit: 100,
        totalPages: 0,
        page: 1,
        pagingCounter: 1,
        hasPrevPage: false,
        hasNextPage: false,
        prevPage: null,
        nextPage: null,
      };
    }
  }
);

export async function getVehicle(id: string): Promise<PayloadVehicle | null> {
  try {
    const payload = await getPayload({ config: configPromise });
    const vehicle = await payload.findByID({
      collection: "vehicles",
      id,
      depth: 1,
    });
    return vehicle as unknown as PayloadVehicle;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Globals — cached across requests with unstable_cache + revalidateTag
// ---------------------------------------------------------------------------

// unstable_cache persists data across requests; React cache() deduplicates
// within a single request. Combine both for maximum efficiency.

const _getSiteSettingsRaw = unstable_cache(
  async (): Promise<SiteSettings> => {
    try {
      const payload = await getPayload({ config: configPromise });
      const settings = await payload.findGlobal({ slug: "siteSettings" });
      return settings as unknown as SiteSettings;
    } catch {
      return {};
    }
  },
  ["site-settings"],
  { tags: ["site-settings"] }
);

export const getSiteSettings = cache(_getSiteSettingsRaw);

const _getHeroContentRaw = unstable_cache(
  async (): Promise<HeroContent> => {
    try {
      const payload = await getPayload({ config: configPromise });
      const hero = await payload.findGlobal({ slug: "heroContent" });
      return hero as unknown as HeroContent;
    } catch {
      return {};
    }
  },
  ["hero-content"],
  { tags: ["hero-content"] }
);

export const getHeroContent = cache(_getHeroContentRaw);

const _getContactPageRaw = unstable_cache(
  async (): Promise<ContactPage> => {
    try {
      const payload = await getPayload({ config: configPromise });
      const page = await payload.findGlobal({ slug: "contactPage" });
      return page as unknown as ContactPage;
    } catch {
      return {};
    }
  },
  ["contact-page"],
  { tags: ["contact-page"] }
);

export const getContactPage = cache(_getContactPageRaw);

// ---------------------------------------------------------------------------
// Posts (blog)
// ---------------------------------------------------------------------------

export async function getPosts(
  opts: { limit?: number; publishedOnly?: boolean } = {}
): Promise<Post[]> {
  try {
    const payload = await getPayload({ config: configPromise });
    const result = await payload.find({
      collection: "posts",
      where: opts.publishedOnly ? { status: { equals: "PUBLISHED" } } : {},
      sort: "-publishedAt",
      limit: opts.limit ?? 10,
      depth: 1,
    });
    return result.docs as unknown as Post[];
  } catch {
    return [];
  }
}

export async function getPost(slug: string): Promise<Post | null> {
  try {
    const payload = await getPayload({ config: configPromise });
    const result = await payload.find({
      collection: "posts",
      where: { slug: { equals: slug } },
      limit: 1,
      depth: 1,
    });
    return (result.docs[0] as unknown as Post) ?? null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Testimonials
// ---------------------------------------------------------------------------

export async function getTestimonials(): Promise<Testimonial[]> {
  try {
    const payload = await getPayload({ config: configPromise });
    const result = await payload.find({
      collection: "testimonials",
      limit: 10,
      depth: 1,
    });
    return result.docs as unknown as Testimonial[];
  } catch {
    return [];
  }
}
