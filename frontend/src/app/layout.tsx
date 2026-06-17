import type { Metadata } from "next";
import { Inter, Bebas_Neue } from "next/font/google";
import "./globals.css";
import { getSiteSettings } from "@/lib/api";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const bebasNeue = Bebas_Neue({
  variable: "--font-bebas",
  weight: "400",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Reliable Used Cars — Quality Pre-Owned Vehicles",
  description:
    "Browse quality pre-owned vehicles at honest prices. Cash only — no financing, no credit checks. Free test drives. Clean titles.",
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  let primaryColor = "#F76C45";
  let primaryColorDark = "#e55a33";
  try {
    const settings = await getSiteSettings();
    primaryColor = settings.primaryColor || primaryColor;
    primaryColorDark = settings.primaryColorDark || primaryColorDark;
  } catch (err) {
    console.error("Failed to load site settings in layout:", err);
  }

  return (
    <html
      lang="en"
      suppressHydrationWarning
      style={
        {
          "--color-primary": primaryColor,
          "--color-primary-dark": primaryColorDark,
        } as React.CSSProperties
      }
    >
      <body className={`${inter.variable} ${bebasNeue.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
