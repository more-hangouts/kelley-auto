import TopBanner from "./components/TopBanner";
import Hero from "./components/Hero";
import PopularCars from "./components/PopularCars";
import Brands from "./components/Brands";
import Testimonials from "./components/Testimonials";
import BlogSection from "./components/BlogSection";
import Features from "./components/Features";
import CTASection from "./components/CTASection";
import Footer from "./components/Footer";
import { getVehicles } from "@/lib/api";

export const revalidate = 60;

export default async function Home() {
  const { docs: vehicles } = await getVehicles({ limit: 100 });

  return (
    <div className="min-h-screen">
      <TopBanner />
      <Hero />
      <PopularCars vehicles={vehicles} />
      <Brands vehicles={vehicles} />
      <Testimonials />
      <BlogSection />
      <Features />
      <CTASection />
      <Footer />
    </div>
  );
}
