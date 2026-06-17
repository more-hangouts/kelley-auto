import { getPayload } from "payload";
import configPromise from "@payload-config";
import { headers } from "next/headers";
import { NextResponse } from "next/server";

/**
 * Verify the request is from an authenticated Payload CMS user.
 * Returns the user on success, or a 401 NextResponse on failure.
 */
export async function requireAuth(): Promise<
  | { user: Record<string, unknown>; error?: never }
  | { user?: never; error: NextResponse }
> {
  try {
    const payload = await getPayload({ config: configPromise });
    const { user } = await payload.auth({ headers: await headers() });

    if (!user) {
      return {
        error: NextResponse.json(
          { error: "Unauthorized" },
          { status: 401 }
        ),
      };
    }

    return { user: user as Record<string, unknown> };
  } catch {
    return {
      error: NextResponse.json(
        { error: "Unauthorized" },
        { status: 401 }
      ),
    };
  }
}
