import Link from "next/link";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";

export const metadata = {
  title: "About Us | Reliable Used Cars",
  description: "A cash-only used car dealership focused on reliable vehicles at honest prices.",
};

export default function AboutPage() {
  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Hero */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-12 md:py-16 lg:py-20">
        <p className="text-sm font-medium text-primary uppercase tracking-wide">Our Story</p>
        <h1 className="mt-2 text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-neutral-700 max-w-2xl">
          Reliable cars at honest prices — no financing, no hassle
        </h1>
        <p className="mt-4 max-w-xl text-base md:text-lg text-neutral-500">
          We started Reliable Used Cars because buying a used car shouldn&apos;t be a stressful experience. Every vehicle on our lot is cash-only, clean-titled, and personally inspected before it goes up for sale.
        </p>
      </section>

      {/* Values */}
      <section className="px-5 md:px-10 lg:px-20 py-12 md:py-16">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {[
            {
              d: "M9 12l2 2 4-4M7.835 4.697a3.42 3.42 0 001.946-.806 3.42 3.42 0 014.438 0 3.42 3.42 0 001.946.806 3.42 3.42 0 013.138 3.138 3.42 3.42 0 00.806 1.946 3.42 3.42 0 010 4.438 3.42 3.42 0 00-.806 1.946 3.42 3.42 0 01-3.138 3.138 3.42 3.42 0 00-1.946.806 3.42 3.42 0 01-4.438 0 3.42 3.42 0 00-1.946-.806 3.42 3.42 0 01-3.138-3.138 3.42 3.42 0 00-.806-1.946 3.42 3.42 0 010-4.438 3.42 3.42 0 00.806-1.946 3.42 3.42 0 013.138-3.138z",
              title: "Clean Titles Only",
              body: "Every vehicle we sell comes with a clean title. No salvage, no rebuilt, no surprises — just straightforward ownership you can trust.",
            },
            {
              d: "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z",
              title: "Cash Only — No Financing",
              body: "We keep it simple. No loans, no interest, no credit checks. Pay cash, get keys. It's the straightforward way to buy a car.",
            },
            {
              d: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z",
              title: "Appointment Only",
              body: "No crowded lots. When you come in, you get our full attention. We schedule viewings one at a time so you can take your time and ask all the questions you need.",
            },
          ].map(({ d, title, body }) => (
            <div key={title} className="flex flex-col gap-4">
              <div className="flex size-12 items-center justify-center rounded-2xl bg-primary/10">
                <svg className="size-6 text-primary" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d={d} />
                </svg>
              </div>
              <h3 className="text-lg font-semibold text-neutral-700">{title}</h3>
              <p className="text-sm text-neutral-500 leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Divider */}
      <div className="border-t border-neutral-50 mx-5 md:mx-10 lg:mx-20" />

      {/* How it works */}
      <section className="px-5 md:px-10 lg:px-20 py-12 md:py-16">
        <h2 className="text-2xl md:text-3xl font-semibold tracking-tight text-neutral-700">
          How it works
        </h2>
        <p className="mt-2 text-base text-neutral-500">Three simple steps — no pressure, no games.</p>
        <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              step: "01",
              title: "Browse the inventory",
              body: "Check out what we have available online. Every listing includes photos, mileage, and an asking price.",
            },
            {
              step: "02",
              title: "Request an appointment",
              body: "Pick a time slot that works for you. We prepare the vehicle before your arrival so you can inspect it without any wait.",
            },
            {
              step: "03",
              title: "Come see it in person",
              body: "Inspect the car, ask questions, take it for a drive. If it's the right fit — pay cash and drive home.",
            },
          ].map(({ step, title, body }) => (
            <div key={step} className="flex flex-col gap-3">
              <span className="font-display text-4xl text-primary/30 leading-none">{step}</span>
              <h3 className="text-base font-semibold text-neutral-700">{title}</h3>
              <p className="text-sm text-neutral-500 leading-relaxed">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="mx-5 md:mx-10 lg:mx-20 mb-12 md:mb-16 rounded-2xl bg-neutral-700 px-6 md:px-10 py-10 md:py-12">
        <h2 className="text-2xl md:text-3xl font-semibold text-white">
          Ready to find your next car?
        </h2>
        <p className="mt-2 text-base text-neutral-300">
          Browse available inventory or reach out to schedule a viewing.
        </p>
        <div className="mt-6 flex flex-col sm:flex-row gap-3">
          <Link
            href="/shop"
            className="inline-flex items-center justify-center rounded-xl bg-primary px-6 py-3 text-sm font-semibold text-white hover:bg-primary-dark transition-colors"
          >
            Browse Inventory
          </Link>
          <Link
            href="/contact"
            className="inline-flex items-center justify-center rounded-xl border border-white/20 bg-white/10 px-6 py-3 text-sm font-semibold text-white hover:bg-white/20 transition-colors"
          >
            Contact Us
          </Link>
        </div>
      </section>

      <Footer />
    </div>
  );
}
