import Link from "next/link";

export default function CTASection() {
  return (
    <section className="relative overflow-hidden bg-neutral-800 px-5 md:px-10 lg:px-20 py-16 md:py-20 lg:py-24">
      {/* Decorative gradient blob */}
      <div className="pointer-events-none absolute -top-24 -right-24 size-96 rounded-full bg-primary/10 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-24 -left-24 size-96 rounded-full bg-primary/5 blur-3xl" />

      <div className="relative mx-auto max-w-[720px] text-center">
        {/* Eyebrow */}
        <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-4">
          Drive Reliable Cars
        </p>

        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight tracking-tight text-white">
          Find Your Next Car — No Financing Needed
        </h2>
        <p className="mt-4 text-base md:text-lg text-neutral-400 leading-relaxed">
          We keep it simple. Browse our lot, pick a car you love, pay cash, and
          drive home the same day. No applications. No credit checks. No hassle.
        </p>

        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
          <Link
            href="/shop"
            className="w-full sm:w-auto rounded-xl bg-primary px-8 py-3.5 text-base font-semibold text-white hover:bg-primary-dark transition-colors"
          >
            Browse Inventory
          </Link>
          <Link
            href="/contact"
            className="w-full sm:w-auto rounded-xl border border-neutral-600 px-8 py-3.5 text-base font-semibold text-neutral-100 hover:border-neutral-400 transition-colors"
          >
            Contact Us
          </Link>
        </div>

        {/* Trust signals */}
        <div className="mt-10 flex flex-wrap items-center justify-center gap-6 text-sm text-neutral-500">
          <span className="flex items-center gap-2">
            <svg className="size-4 text-primary" fill="none" viewBox="0 0 16 16">
              <path d="M3 8l3 3 7-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Clean titles
          </span>
          <span className="flex items-center gap-2">
            <svg className="size-4 text-primary" fill="none" viewBox="0 0 16 16">
              <path d="M3 8l3 3 7-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Cash only
          </span>
          <span className="flex items-center gap-2">
            <svg className="size-4 text-primary" fill="none" viewBox="0 0 16 16">
              <path d="M3 8l3 3 7-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Free test drives
          </span>
          <span className="flex items-center gap-2">
            <svg className="size-4 text-primary" fill="none" viewBox="0 0 16 16">
              <path d="M3 8l3 3 7-7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            No-pressure sales
          </span>
        </div>
      </div>
    </section>
  );
}
