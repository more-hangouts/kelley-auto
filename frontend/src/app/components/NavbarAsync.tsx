import Navbar from "./Navbar";
import { getSiteSettings } from "@/lib/api";

/** Internal async component — fetches CMS phone and renders Navbar. */
export async function NavbarAsync({ light }: { light?: boolean }) {
  const settings = await getSiteSettings();
  return <Navbar light={light} phone={settings.phone} />;
}
