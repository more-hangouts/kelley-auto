import { Suspense } from "react";
import Navbar from "./Navbar";
import { NavbarAsync } from "./NavbarAsync";

/**
 * Sync server component that wraps the async NavbarAsync in a Suspense
 * boundary. This lets the rest of the page stream while the navbar resolves,
 * and prevents a CMS hiccup from blocking the entire layout.
 */
export default function NavbarWrapper({ light }: { light?: boolean }) {
  return (
    <Suspense fallback={<Navbar light={light} />}>
      <NavbarAsync light={light} />
    </Suspense>
  );
}
