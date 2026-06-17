import { NextRequest, NextResponse } from "next/server";
import { getPayload } from "payload";
import configPromise from "@payload-config";
import { Resend } from "resend";
import { escapeHtml } from "@/lib/sanitize";

const resend = new Resend(process.env.RESEND_API_KEY);

// Simple in-memory rate limiter: max 5 submissions per IP per 15 minutes
const rateMap = new Map<string, number[]>();
const RATE_WINDOW_MS = 15 * 60 * 1000;
const RATE_MAX = 5;

function isRateLimited(ip: string): boolean {
  const now = Date.now();
  const timestamps = (rateMap.get(ip) || []).filter(
    (t) => now - t < RATE_WINDOW_MS
  );
  if (timestamps.length >= RATE_MAX) return true;
  timestamps.push(now);
  rateMap.set(ip, timestamps);
  return false;
}

export async function POST(req: NextRequest) {
  // Rate limit by IP
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip") ||
    "unknown";
  if (isRateLimited(ip)) {
    return NextResponse.json(
      { error: "Too many requests. Please try again later." },
      { status: 429 }
    );
  }

  try {
    const body = await req.json();
    const { firstName, lastName, email, phone, message, vehicle, preferredTime } = body;

    if (!firstName || !lastName || !email) {
      return NextResponse.json(
        { error: "firstName, lastName, and email are required" },
        { status: 400 }
      );
    }

    // Basic email format validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      return NextResponse.json(
        { error: "Invalid email format" },
        { status: 400 }
      );
    }

    const payload = await getPayload({ config: configPromise });

    const inquiryData: Record<string, unknown> = {
      firstName,
      lastName,
      email,
      phone,
      message,
      preferredTime,
    };
    if (vehicle) inquiryData.vehicle = vehicle;

    const inquiry = await payload.create({
      collection: "inquiries",
      data: inquiryData,
    });

    // Send email notification
    try {
      let vehicleLabel = "General inquiry";
      let vehicleDoc: Record<string, unknown> | null = null;

      if (vehicle) {
        vehicleDoc = await payload.findByID({
          collection: "vehicles",
          id: vehicle,
          depth: 0,
        }) as Record<string, unknown>;
        vehicleLabel = `${vehicleDoc.year} ${vehicleDoc.make} ${vehicleDoc.model}`;
      }

      await resend.emails.send({
        from: process.env.EMAIL_FROM || "Reliable Cars <noreply@drivereliablecars.com>",
        to: (process.env.EMAIL_TO || "sales@drivereliablecars.com,demar@drivereliablecars.com")
          .split(",")
          .map((e) => e.trim()),
        subject: `New Appointment Request${vehicle ? `: ${vehicleLabel}` : ""}`,
        html: `
          <h2>New Appointment Request</h2>
          <p><strong>Customer:</strong> ${escapeHtml(firstName)} ${escapeHtml(lastName)}</p>
          <p><strong>Email:</strong> ${escapeHtml(email)}</p>
          ${phone ? `<p><strong>Phone:</strong> ${escapeHtml(phone)}</p>` : ""}
          ${vehicle && vehicleDoc ? `<p><strong>Vehicle:</strong> ${escapeHtml(vehicleLabel)}${vehicleDoc.vin ? ` (VIN: ${escapeHtml(String(vehicleDoc.vin))})` : ""}</p>` : ""}
          ${preferredTime ? `<p><strong>Preferred Time:</strong> ${escapeHtml(preferredTime)}</p>` : ""}
          ${message ? `<p><strong>Notes:</strong></p><p>${escapeHtml(message)}</p>` : ""}
        `,
      });
    } catch (emailError) {
      console.error("Failed to send email notification:", emailError);
    }

    return NextResponse.json({ success: true, id: inquiry.id }, { status: 201 });
  } catch (error) {
    console.error("Inquiry submission error:", error);
    return NextResponse.json(
      { error: "Failed to submit inquiry" },
      { status: 500 }
    );
  }
}
