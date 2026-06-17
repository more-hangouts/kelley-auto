import type { MediaDoc } from "./vehicle";

export type SiteSettings = {
  businessName?: string;
  phone?: string;
  email?: string;
  address?: string;
  city?: string;
  bannerLabel?: string;
  bannerText?: string;
  primaryColor?: string;
  primaryColorDark?: string;
};

export type HeroContent = {
  watermark?: string;
  subheadline?: string;
  ctaLabel?: string;
  ctaHref?: string;
  headline?: string;
  showCarImage?: boolean;
  /** Populated at depth=1; may be a string ID at depth=0 */
  bgImage?: MediaDoc | string | null;
  /** Populated at depth=1; may be a string ID at depth=0 */
  carImage?: MediaDoc | string | null;
};

export type Post = {
  id: string;
  title: string;
  slug: string;
  excerpt?: string | null;
  /** Populated at depth=1; may be a string ID at depth=0 */
  coverImage?: MediaDoc | string | null;
  body?: unknown;
  author?: string | null;
  readTime?: string | null;
  publishedAt?: string | null;
  status?: "DRAFT" | "PUBLISHED";
  createdAt: string;
  updatedAt: string;
};

export type Testimonial = {
  id: string;
  name: string;
  quote: string;
  rating?: number | null;
  /** Populated at depth=1; may be a string ID at depth=0 */
  photo?: MediaDoc | string | null;
  vehiclePurchased?: string | null;
};

export type ContactPage = {
  tagline?: string;
  heading?: string;
  /** Rich text (Lexical JSON) */
  description?: unknown;
  /** Rich text (Lexical JSON) */
  appointmentNote?: unknown;
  formHeading?: string;
  mondayFriday?: string;
  saturday?: string;
  sunday?: string;
};

// ---------------------------------------------------------------------------
// Type guard helpers for Payload relationship fields
// ---------------------------------------------------------------------------

/** Narrows a Payload relationship field to its populated MediaDoc form. */
export function isMediaDoc(value: MediaDoc | string | null | undefined): value is MediaDoc {
  return typeof value === "object" && value !== null;
}
