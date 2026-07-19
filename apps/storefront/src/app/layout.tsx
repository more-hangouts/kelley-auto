import type { Metadata } from "next";
import { Inter, Bebas_Neue } from "next/font/google";
import "./globals.css";
import { getSiteSettings } from "@/lib/api";
import { SITE_URL } from "@/lib/site";
import ChatWidget from "./components/ChatWidget";
import GoogleAnalytics from "./components/GoogleAnalytics";
import MetaPixel from "./components/MetaPixel";
import PageViewTracker from "./components/PageViewTracker";

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
  metadataBase: new URL(SITE_URL),
  title: "Kelley Autoplex: Car Dealer in San Antonio, TX",
  description:
    "Kelley Autoplex is here to serve as your ultimate vehicle consultant. Sales, service, financing, whatever your needs, we are here to serve you.",
  alternates: {
    canonical: "/",
  },
  openGraph: {
    title: "Kelley Autoplex: Car Dealer in San Antonio, TX",
    description:
      "Kelley Autoplex is here to serve as your ultimate vehicle consultant. Sales, service, financing, whatever your needs, we are here to serve you.",
    siteName: "Kelley Autoplex",
    type: "website",
  },
};

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  let primaryColor = "#157A33";
  let primaryColorDark = "#0F5E26";
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
        <PageViewTracker />
        <GoogleAnalytics />
        <MetaPixel />
        <ChatWidget />
      </body>
    </html>
  );
}
