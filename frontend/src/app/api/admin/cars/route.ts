import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/auth-guard";

export async function GET(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.error) return auth.error;

  try {
    const cars = await prisma.car.findMany({
      include: {
        images: { orderBy: { sortOrder: "asc" } },
        _count: { select: { inquiries: true } },
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

export async function POST(req: NextRequest) {
  const auth = await requireAuth();
  if (auth.error) return auth.error;

  try {
    const body = await req.json();
    const {
      vin,
      make,
      model,
      year,
      cashPrice,
      mileage,
      condition,
      exteriorColor,
      interiorColor,
      transmission,
      fuelType,
      description,
      status,
      images,
    } = body;

    if (
      !vin ||
      !make ||
      !model ||
      !year ||
      !cashPrice ||
      mileage == null ||
      !condition ||
      !exteriorColor ||
      !interiorColor ||
      !transmission ||
      !fuelType
    ) {
      return NextResponse.json(
        { error: "Missing required fields" },
        { status: 400 }
      );
    }

    const car = await prisma.car.create({
      data: {
        vin,
        make,
        model,
        year,
        cashPrice,
        mileage,
        condition,
        exteriorColor,
        interiorColor,
        transmission,
        fuelType,
        description,
        status,
        ...(images?.length && {
          images: {
            create: images.map(
              (
                img: { imageUrl: string; isPrimary?: boolean; sortOrder?: number },
                index: number
              ) => ({
                imageUrl: img.imageUrl,
                isPrimary: img.isPrimary ?? index === 0,
                sortOrder: img.sortOrder ?? index,
              })
            ),
          },
        }),
      },
      include: { images: true },
    });

    return NextResponse.json(car, { status: 201 });
  } catch (error: any) {
    if (error?.code === "P2002") {
      return NextResponse.json(
        { error: "A car with this VIN already exists" },
        { status: 409 }
      );
    }
    return NextResponse.json(
      { error: "Failed to create car" },
      { status: 500 }
    );
  }
}
