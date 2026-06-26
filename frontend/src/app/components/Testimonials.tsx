import { getTestimonials } from "@/lib/api";

function initials(name: string): string {
  return (
    (name || "")
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((p) => p[0]?.toUpperCase() || "")
      .join("") || "?"
  );
}

const FALLBACK = [
  {
    id: "1",
    name: "Michael Reyes",
    photo: null as null,
    rating: 4.8,
    quote:
      "The buying process was smooth with clear and transparent pricing. The car arrived in excellent condition and matched the description perfectly.",
  },
  {
    id: "2",
    name: "Sarah Johnson",
    photo: null as null,
    rating: 5.0,
    quote:
      "I was nervous buying a used car, but the team made it so easy. They walked me through every step and I drove away feeling confident in my purchase.",
  },
  {
    id: "3",
    name: "David Kim",
    photo: null as null,
    rating: 4.9,
    quote:
      "Best dealership experience I've ever had. No pressure, fair pricing, and the car was exactly as described. Highly recommend to anyone looking for a reliable ride.",
  },
];

export default async function Testimonials() {
  const fromCms = await getTestimonials();
  const list = fromCms.length > 0 ? fromCms : FALLBACK;

  return (
    <section className="bg-primary px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      <div className="mx-auto max-w-[800px] text-center">
        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-white">
          Customer Testimonials
        </h2>
        <p className="mt-3 md:mt-4 text-base md:text-lg text-neutral-50">
          Real experiences from customers who found their car with confidence and ease.
        </p>
      </div>

      <div className="mt-8 md:mt-12 flex gap-6 overflow-x-auto snap-x snap-mandatory pb-4 md:pb-0 md:snap-none md:overflow-visible md:max-w-[1200px] md:mx-auto md:flex-col md:gap-8">
        {list.map((t) => {
          return (
            <div
              key={t.id}
              className="min-w-[90%] snap-center md:min-w-full md:flex-none flex flex-col md:flex-row gap-6"
            >
              {/* Avatar — initials (no stock photo; swap to a real image later) */}
              <div className="relative flex h-[250px] md:h-[298px] w-full md:w-[324px] shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-white/15">
                <span className="text-5xl md:text-6xl font-semibold tracking-wide text-white/90">
                  {initials(t.name)}
                </span>
              </div>

              {/* Quote card */}
              <div className="flex flex-1 flex-col justify-between overflow-hidden rounded-2xl border border-neutral-50/30 bg-white/10 p-5 md:p-8 gap-6 md:gap-0">
                <p className="text-xl md:text-2xl lg:text-[30px] font-medium leading-7 md:leading-8 lg:leading-[38px] text-white">
                  &ldquo;{t.quote}&rdquo;
                </p>

                <div className="flex flex-col sm:flex-row items-start sm:items-end justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-base md:text-lg text-white">{t.name}</span>
                      <span className="size-1 rounded-full bg-white/50" />
                      <svg
                        className="size-5 text-yellow-400"
                        fill="currentColor"
                        viewBox="0 0 20 20"
                      >
                        <path d="M10 2l2.22 4.5 4.97.73-3.6 3.5.85 4.94L10 13.04l-4.44 2.33.85-4.94-3.6-3.5 4.97-.73L10 2Z" />
                      </svg>
                      {t.rating != null && (
                        <span className="text-sm font-medium text-white">{t.rating}</span>
                      )}
                    </div>
                    <p className="mt-1 text-sm md:text-base text-neutral-25">
                      {"vehiclePurchased" in t && t.vehiclePurchased
                        ? `Purchased: ${t.vehiclePurchased}`
                        : "Verified Buyer"}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
