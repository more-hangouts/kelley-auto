import Navbar from "./Navbar";
import { resolveNap } from "@/lib/nap";

/** Internal async component — resolves business NAP and renders Navbar. */
export async function NavbarAsync({ light }: { light?: boolean }) {
  const nap = await resolveNap();
  return (
    <Navbar light={light} phone={nap.phoneDisplay} telHref={nap.telHref} />
  );
}
