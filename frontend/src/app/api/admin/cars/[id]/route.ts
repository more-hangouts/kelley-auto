import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { requireAuth } from "@/lib/auth-guard";

export async function PATCH(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const auth = await requireAuth();
  if (auth.error) return auth.error;

  try {
    const { id } = await params;
    const body = await req.json();

    const existing = await prisma.car.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json({ error: "Car not found" }, { status: 404 });
    }

    const { images, ...carData } = body;

    const car = await prisma.car.update({
      where: { id },
      data: {
        ...carData,
        ...(images && {
          images: {
            deleteMany: {},
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
      include: { images: { orderBy: { sortOrder: "asc" } } },
    });

    return NextResponse.json(car);
  } catch (error: any) {
    if (error?.code === "P2002") {
      return NextResponse.json(
        { error: "A car with this VIN already exists" },
        { status: 409 }
      );
    }
    return NextResponse.json(
      { error: "Failed to update car" },
      { status: 500 }
    );
  }
}

export async function DELETE(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const auth = await requireAuth();
  if (auth.error) return auth.error;

  try {
    const { id } = await params;

    const existing = await prisma.car.findUnique({ where: { id } });
    if (!existing) {
      return NextResponse.json({ error: "Car not found" }, { status: 404 });
    }

    await prisma.car.delete({ where: { id } });

    return NextResponse.json({ message: "Car deleted successfully" });
  } catch (error) {
    return NextResponse.json(
      { error: "Failed to delete car" },
      { status: 500 }
    );
  }
}
