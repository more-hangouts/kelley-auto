const features = [
  {
    title: "CASH ONLY",
    description: "No financing, no credit checks",
    icon: (
      <svg className="size-8 md:size-10 text-neutral-400" fill="none" viewBox="0 0 40 40">
        <rect x="5" y="12" width="30" height="18" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="20" cy="21" r="4" stroke="currentColor" strokeWidth="1.5" />
        <path d="M5 17h4M31 17h4M5 25h4M31 25h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    title: "FREE TEST DRIVES",
    description: "Come see any car on the lot",
    icon: (
      <svg className="size-8 md:size-10 text-neutral-400" fill="none" viewBox="0 0 40 40">
        <path d="M7 22l3-8h20l3 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <rect x="5" y="22" width="30" height="8" rx="2" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="32" r="2.5" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="28" cy="32" r="2.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M14 18h4M17 14v8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    title: "CLEAN TITLES",
    description: "Every vehicle has a clean title",
    icon: (
      <svg className="size-8 md:size-10 text-neutral-400" fill="none" viewBox="0 0 40 40">
        <path d="M20 5l3.5 9.5H34l-8 5.5 3 9.5-8-5.5-8 5.5 3-9.5-8-5.5h10.5L20 5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    title: "LOCAL DEALER",
    description: "Family-owned, no-pressure sales",
    icon: (
      <svg className="size-8 md:size-10 text-neutral-400" fill="none" viewBox="0 0 40 40">
        <path d="M20 5C14.48 5 10 9.48 10 15c0 8.75 10 20 10 20s10-11.25 10-20c0-5.52-4.48-10-10-10Z" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="20" cy="15" r="3" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
];

export default function Features() {
  return (
    <section className="bg-neutral-25 px-5 md:px-10 lg:px-[72px] py-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-0 items-center justify-center">
        {features.map((feature, i) => (
          <div key={feature.title} className="flex items-center">
            <div className="flex flex-1 items-center gap-3 md:gap-4 px-2 md:px-3 py-3 md:py-4">
              {feature.icon}
              <div>
                <p className="text-xs md:text-sm font-medium text-neutral-700">
                  {feature.title}
                </p>
                <p className="text-xs md:text-sm text-neutral-500">
                  {feature.description}
                </p>
              </div>
            </div>
            {i < features.length - 1 && (
              <div className="hidden md:block h-14 w-px bg-neutral-100" />
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
