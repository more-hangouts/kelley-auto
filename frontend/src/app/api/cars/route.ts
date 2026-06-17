import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);

  const make = searchParams.get("make");
  const model = searchParams.get("model");
  const fuelType = searchParams.get("fuelType");
  const transmission = searchParams.get("transmission");
  const minPrice = searchParams.get("minPrice");
  const maxPrice = searchParams.get("maxPrice");
  const minYear = searchParams.get("minYear");
  const maxYear = searchParams.get("maxYear");
  const minMileage = searchParams.get("minMileage");
  const maxMileage = searchParams.get("maxMileage");

  try {
    const cars = await prisma.car.findMany({
      where: {
        status: "AVAILABLE",
        ...(make && { make: { contains: make, mode: "insensitive" } }),
        ...(model && { model: { contains: model, mode: "insensitive" } }),
        ...(fuelType && { fuelType: fuelType as any }),
        ...(transmission && { transmission: transmission as any }),
        ...(minPrice || maxPrice
          ? {
              cashPrice: {
                ...(minPrice && { gte: parseFloat(minPrice) }),
                ...(maxPrice && { lte: parseFloat(maxPrice) }),
              },
            }
          : {}),
        ...(minYear || maxYear
          ? {
              year: {
                ...(minYear && { gte: parseInt(minYear) }),
                ...(maxYear && { lte: parseInt(maxYear) }),
              },
            }
          : {}),
        ...(minMileage || maxMileage
          ? {
              mileage: {
                ...(minMileage && { gte: parseInt(minMileage) }),
                ...(maxMileage && { lte: parseInt(maxMileage) }),
              },
            }
          : {}),
      },
      include: {
        images: {
          where: { isPrimary: true },
          take: 1,
        },
      },
      orderBy: { createdAt: "desc" },
    });

    return NextResponse.json(cars);
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to fetch cars" },
      { status: 500 }
    );
  }
}
