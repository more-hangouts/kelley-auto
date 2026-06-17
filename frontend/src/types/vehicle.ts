// Payload CMS REST API response types

export type MediaDoc = {
  id: string;
  url: string;
  alt?: string | null;
  filename: string;
  mimeType: string;
  width?: number | null;
  height?: number | null;
};

export type PayloadVehicle = {
  id: string;
  title: string;
  vin?: string | null;
  make: string;
  model: string;
  /** Stored as a select string in Payload — e.g. "2018" */
  year: string;
  cashPrice?: number | null;
  mileage?: number | null;
  condition?: "NEW" | "USED" | null;
  exteriorColor?: string | null;
  exteriorColorCustom?: string | null;
  interiorColor?: string | null;
  interiorColorCustom?: string | null;
  transmission?: "AUTOMATIC" | "MANUAL" | null;
  fuelType?: "GAS" | "DIESEL" | "ELECTRIC" | "HYBRID" | null;
  /** Lexical rich text — use lexicalToText() to get plain string */
  description?: unknown;
  status?: "AVAILABLE" | "PENDING" | "SOLD" | null;
  /** Populated at depth=1 */
  photos?: MediaDoc[];
  createdAt: string;
  updatedAt: string;
};

export type PayloadListResponse<T> = {
  docs: T[];
  totalDocs: number;
  limit: number;
  totalPages: number;
  page: number;
  pagingCounter: number;
  hasPrevPage: boolean;
  hasNextPage: boolean;
  prevPage: number | null;
  nextPage: number | null;
};
