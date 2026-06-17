/**
 * Pure vehicle helper functions — no Payload/Node.js imports.
 * Safe to import from client components (e.g. VehicleCard).
 */
import type { PayloadVehicle } from "@/types/vehicle";

// ---------------------------------------------------------------------------
// Lexical rich text → plain text
// ---------------------------------------------------------------------------

type LexicalNode = {
  type?: string;
  text?: string;
  children?: LexicalNode[];
};

function walkNodes(node: LexicalNode): string {
  if (node.type === "text" && typeof node.text === "string") return node.text;
  if (Array.isArray(node.children)) {
    return node.children.map(walkNodes).join(" ");
  }
  return "";
}

export function lexicalToText(richText: unknown): string {
  if (!richText || typeof richText !== "object") return "";
  const root = (richText as { root?: LexicalNode }).root;
  if (!root) return "";
  return walkNodes(root).replace(/\s+/g, " ").trim();
}

// ---------------------------------------------------------------------------
// Photo helpers
// ---------------------------------------------------------------------------

export function primaryPhoto(vehicle: PayloadVehicle): string | null {
  return vehicle.photos?.[0]?.url ?? null;
}

export function allPhotos(vehicle: PayloadVehicle): string[] {
  return (vehicle.photos ?? []).map((p) => p.url).filter(Boolean);
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------

export function displayYear(vehicle: PayloadVehicle): string {
  return vehicle.year ?? "";
}

export function displayColor(vehicle: PayloadVehicle): string {
  if (!vehicle.exteriorColor) return "";
  if (vehicle.exteriorColor === "Other") return vehicle.exteriorColorCustom ?? "Other";
  return vehicle.exteriorColor;
}

export function isSold(vehicle: PayloadVehicle): boolean {
  return vehicle.status === "SOLD";
}
