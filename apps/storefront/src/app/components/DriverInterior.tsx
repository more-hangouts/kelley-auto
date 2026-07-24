import Image from "next/image";

export default function DriverInterior() {
  return (
    <section className="bg-white px-5 md:px-10 lg:px-20 py-10 md:py-16 lg:py-20">
      <div className="mx-auto max-w-[800px] text-center">
        <h2 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight lg:leading-[60px] tracking-tight text-neutral-700">
          Driver Focused Interior
        </h2>
        <p className="mt-3 md:mt-4 text-base md:text-lg text-neutral-600">
          Every detail engineered for control and comfort.
        </p>
      </div>

      <div className="relative mt-8 md:mt-14 flex flex-col md:flex-row h-auto md:h-[400px] lg:h-[578px] overflow-hidden rounded-2xl gap-1 md:gap-0">
        {/* Left image */}
        <div className="relative h-[250px] md:h-full md:flex-1">
          <Image
            src="/images/interior-left.jpg"
            alt="Tacora Red interior"
            fill
            className="object-cover"
          />
          <div className="absolute bottom-4 left-4 md:bottom-8 md:left-9 text-white">
            <p className="text-base md:text-xl font-medium">BMW 3 Series Sedan</p>
            <p className="text-xl md:text-[30px] font-semibold leading-6 md:leading-[38px]">Tacora Red</p>
          </div>
        </div>

        {/* Divider with icon */}
        <div className="hidden md:flex absolute inset-y-0 left-1/2 z-10 -translate-x-1/2 items-center">
          <div className="flex size-10 items-center justify-center rounded-full bg-white shadow-md">
            <svg className="size-6 text-neutral-700" fill="none" viewBox="0 0 24 24">
              <path d="M4 12h16M4 6h16M4 18h16" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </div>
        </div>

        {/* Right image */}
        <div className="relative h-[250px] md:h-full md:flex-1 bg-white">
          <Image
            src="/images/interior-right.jpg"
            alt="Black Decor Stitching interior"
            fill
            className="object-cover"
          />
          <div className="absolute bottom-4 right-4 md:bottom-8 md:right-9 text-right text-white">
            <p className="text-base md:text-xl font-medium">BMW 3 Series Sedan</p>
            <p className="text-xl md:text-[30px] font-semibold leading-6 md:leading-[38px]">Black Decor Stitching</p>
          </div>
        </div>
      </div>
    </section>
  );
}
