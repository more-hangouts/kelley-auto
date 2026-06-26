import Link from "next/link";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import { resolveNap } from "@/lib/nap";

const steps = [
  {
    number: "1",
    title: "Choose your vehicle",
    description:
      "Browse our inventory online or visit our lot to find the right car for you.",
  },
  {
    number: "2",
    title: "Secure financing",
    description:
      "Use your own bank or credit union for the best rates, or explore one of our recommended lenders.",
  },
  {
    number: "3",
    title: "Finalize and drive away",
    description:
      "Complete your paperwork, pick up your vehicle, and hit the road with confidence.",
  },
];

const bankSteps = [
  "Contact your bank or credit union about an auto loan",
  "Share the vehicle details (year, make, model, VIN, price)",
  "Get pre-approved before visiting us",
  "Bring your approval letter — we handle the rest",
];

const goodCreditLenders = [
  {
    name: "RBFCU",
    descriptor: "Local credit union with competitive auto rates",
    url: "https://www.rbfcu.org",
  },
  {
    name: "USAA",
    descriptor: "Trusted by military families nationwide",
    url: "https://www.usaa.com",
  },
  {
    name: "Navy Federal",
    descriptor: "Flexible terms for military and family members",
    url: "https://www.navyfederal.org",
  },
  {
    name: "Security Service",
    descriptor: "Texas-based credit union",
    url: "https://www.ssfcu.org",
  },
  {
    name: "Firstmark",
    descriptor: "Well-known local credit union",
    url: "https://www.firstmarkcu.org",
  },
  {
    name: "Credit Human",
    descriptor: "Community-focused with flexible lending",
    url: "https://www.credithuman.com",
  },
];

const faqs = [
  {
    question: "Can I get approved with bad credit?",
    answer:
      "Yes. While we recommend starting with your own bank, we also work with lenders like Lendmark Financial who specialize in helping people with lower credit scores. Your options may vary, but we are happy to help you explore them.",
  },
  {
    question: "Do I need a down payment?",
    answer:
      "It depends on your lender. Many banks and credit unions offer flexible terms. A down payment can help lower your monthly payment and improve your approval odds, but it is not always required.",
  },
  {
    question: "How long does approval take?",
    answer:
      "If you are pre-approved through your bank or credit union, financing can be completed the same day. Third-party lender approvals typically take one to three business days.",
  },
  {
    question: "Can I pay cash instead?",
    answer:
      "Absolutely. We accept cash, credit cards, and trade-ins. Financing is simply one of several options available to you.",
  },
];

const CheckIcon = () => (
  <svg
    className="size-5 shrink-0 text-primary"
    fill="none"
    viewBox="0 0 16 16"
  >
    <path
      d="M3 8l3 3 7-7"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

export default async function FinancingPage() {
  const nap = await resolveNap();
  // Where the "Apply"/"Call" CTAs point: the phone if we have one, else the
  // contact page. Never a hardcoded number.
  const callHref = nap.telHref || "/contact";
  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* SECTION 1: Hero */}
      <section className="bg-neutral-800 px-5 md:px-10 lg:px-20 py-16 md:py-20 lg:py-28">
        <div className="mx-auto max-w-[720px] text-center">
          <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-4">
            {nap.name}
          </p>
          <h1 className="text-3xl md:text-4xl lg:text-5xl font-semibold leading-tight tracking-tight text-white">
            Financing made simple
          </h1>
          <p className="mt-4 text-base md:text-lg text-neutral-400 leading-relaxed">
            Choose the option that works best for you. We support you every step
            of the way.
          </p>

          <div className="mt-8 flex flex-wrap items-center justify-center gap-x-6 gap-y-3 text-sm text-neutral-300">
            <span className="flex items-center gap-2">
              <CheckIcon />
              Use your own bank or credit union
            </span>
            <span className="flex items-center gap-2">
              <CheckIcon />
              We accept trade-ins
            </span>
            <span className="flex items-center gap-2">
              <CheckIcon />
              Multiple financing options
            </span>
          </div>

          <div className="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4">
            <a
              href={callHref}
              className="w-full sm:w-auto rounded-xl bg-primary px-8 py-3.5 text-base font-semibold text-white hover:bg-primary-dark transition-colors"
            >
              Apply for financing
            </a>
            <a
              href={callHref}
              className="w-full sm:w-auto rounded-xl border border-neutral-600 px-8 py-3.5 text-base font-semibold text-neutral-100 hover:border-neutral-400 transition-colors"
            >
              {nap.phone ? `Call ${nap.phoneDisplay}` : "Contact us"}
            </a>
          </div>
        </div>
      </section>

      {/* SECTION 2: How Financing Works */}
      <section className="bg-white px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[960px]">
          <h2 className="text-center text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            How financing works
          </h2>
          <p className="mt-3 text-center text-base md:text-lg text-neutral-500">
            Three simple steps from browsing to driving home.
          </p>

          <div className="mt-10 md:mt-14 grid grid-cols-1 md:grid-cols-3 gap-6 md:gap-8">
            {steps.map((step) => (
              <div
                key={step.number}
                className="rounded-2xl border border-neutral-50 p-6 md:p-8"
              >
                <div className="flex size-12 items-center justify-center rounded-xl bg-primary/10">
                  <span className="text-lg font-semibold text-primary">
                    {step.number}
                  </span>
                </div>
                <h3 className="mt-4 text-lg md:text-xl font-semibold text-neutral-700">
                  {step.title}
                </h3>
                <p className="mt-2 text-sm md:text-base text-neutral-500 leading-6">
                  {step.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* SECTION 3: Preferred Option — Your Bank */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[960px]">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16 items-start">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-3">
                Recommended
              </p>
              <h2 className="text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
                Use your own bank or credit union
              </h2>
              <p className="mt-4 text-base md:text-lg text-neutral-500 leading-relaxed">
                This is often the smartest option. Your existing bank already
                knows you, which means better rates, more control over your
                loan terms, and faster approvals — especially if you are
                pre-approved.
              </p>

              <div className="mt-6 flex flex-col gap-3">
                <div className="flex items-start gap-3">
                  <CheckIcon />
                  <p className="text-base text-neutral-600">
                    <span className="font-medium">Better rates</span> — credit
                    unions often beat dealer financing
                  </p>
                </div>
                <div className="flex items-start gap-3">
                  <CheckIcon />
                  <p className="text-base text-neutral-600">
                    <span className="font-medium">More control</span> — you
                    choose your terms and lender
                  </p>
                </div>
                <div className="flex items-start gap-3">
                  <CheckIcon />
                  <p className="text-base text-neutral-600">
                    <span className="font-medium">Faster process</span> —
                    pre-approval lets you shop with confidence
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-neutral-100 bg-white p-6 md:p-8">
              <h3 className="text-lg font-semibold text-neutral-700">
                How to get started
              </h3>
              <ol className="mt-5 flex flex-col gap-4">
                {bankSteps.map((step, i) => (
                  <li key={i} className="flex items-start gap-3">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                      {i + 1}
                    </span>
                    <p className="text-base text-neutral-600">{step}</p>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 4: Financing Options by Credit Profile */}
      <section className="bg-white px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[960px]">
          <h2 className="text-center text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            Financing options by credit profile
          </h2>
          <p className="mt-3 text-center text-base md:text-lg text-neutral-500">
            We work with lenders across the credit spectrum.
          </p>

          {/* Good Credit */}
          <div className="mt-10 md:mt-14">
            <div className="flex items-center gap-3 mb-6">
              <div className="flex size-10 items-center justify-center rounded-xl bg-green-50">
                <svg
                  className="size-5 text-green-600"
                  fill="none"
                  viewBox="0 0 20 20"
                >
                  <path
                    d="M4 10l4 4 8-8"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <div>
                <h3 className="text-xl font-semibold text-neutral-700">
                  Good credit (600+)
                </h3>
                <p className="text-sm text-neutral-500">
                  Recommended banks and credit unions
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {goodCreditLenders.map((lender) => (
                <div
                  key={lender.name}
                  className="flex flex-col justify-between rounded-2xl border border-neutral-50 p-5 md:p-6"
                >
                  <div>
                    <h4 className="text-base font-semibold text-neutral-700">
                      {lender.name}
                    </h4>
                    <p className="mt-1 text-sm text-neutral-500">
                      {lender.descriptor}
                    </p>
                  </div>
                  <a
                    href={lender.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:text-primary-dark transition-colors"
                  >
                    Apply online
                    <svg
                      className="size-4"
                      fill="none"
                      viewBox="0 0 20 20"
                    >
                      <path
                        d="M4.17 10h11.66M10 4.17 15.83 10 10 15.83"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      />
                    </svg>
                  </a>
                </div>
              ))}
            </div>
          </div>

          {/* Lower Credit */}
          <div className="mt-12">
            <div className="flex items-center gap-3 mb-6">
              <div className="flex size-10 items-center justify-center rounded-xl bg-amber-50">
                <svg
                  className="size-5 text-amber-600"
                  fill="none"
                  viewBox="0 0 20 20"
                >
                  <path
                    d="M10 3v8M10 14v1"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                  />
                </svg>
              </div>
              <div>
                <h3 className="text-xl font-semibold text-neutral-700">
                  Building credit (below 600)
                </h3>
                <p className="text-sm text-neutral-500">
                  Alternative option if you are still building credit
                </p>
              </div>
            </div>

            <div className="max-w-[480px] rounded-2xl border border-neutral-50 p-5 md:p-6">
              <h4 className="text-base font-semibold text-neutral-700">
                Lendmark Financial
              </h4>
              <p className="mt-1 text-sm text-neutral-500">
                Specializes in helping customers with limited or lower credit
                histories get behind the wheel.
              </p>
              <a
                href="https://www.lendmarkfinancial.com"
                target="_blank"
                rel="noopener noreferrer"
                className="mt-4 inline-flex items-center gap-1.5 text-sm font-medium text-primary hover:text-primary-dark transition-colors"
              >
                Learn more
                <svg className="size-4" fill="none" viewBox="0 0 20 20">
                  <path
                    d="M4.17 10h11.66M10 4.17 15.83 10 10 15.83"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 4.5: Comparison — Your Bank vs Dealer Financing */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[720px]">
          <h2 className="text-center text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            Your bank vs. dealer financing
          </h2>
          <p className="mt-3 text-center text-base md:text-lg text-neutral-500">
            A quick comparison to help you decide.
          </p>

          <div className="mt-10 overflow-hidden rounded-2xl border border-neutral-100 bg-white">
            <table className="w-full text-left text-sm md:text-base">
              <thead>
                <tr className="border-b border-neutral-100 bg-neutral-25">
                  <th className="px-5 py-4 font-semibold text-neutral-700" />
                  <th className="px-5 py-4 font-semibold text-neutral-700">
                    Your bank / credit union
                  </th>
                  <th className="px-5 py-4 font-semibold text-neutral-700">
                    Dealer financing
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-neutral-50">
                <tr>
                  <td className="px-5 py-4 font-medium text-neutral-600">
                    Rates
                  </td>
                  <td className="px-5 py-4 text-neutral-600">
                    Typically lower
                  </td>
                  <td className="px-5 py-4 text-neutral-500">Varies</td>
                </tr>
                <tr>
                  <td className="px-5 py-4 font-medium text-neutral-600">
                    Control
                  </td>
                  <td className="px-5 py-4 text-neutral-600">
                    You choose your terms
                  </td>
                  <td className="px-5 py-4 text-neutral-500">
                    Limited options
                  </td>
                </tr>
                <tr>
                  <td className="px-5 py-4 font-medium text-neutral-600">
                    Speed
                  </td>
                  <td className="px-5 py-4 text-neutral-600">
                    Fast if pre-approved
                  </td>
                  <td className="px-5 py-4 text-neutral-500">
                    Same day possible
                  </td>
                </tr>
                <tr>
                  <td className="px-5 py-4 font-medium text-neutral-600">
                    Credit impact
                  </td>
                  <td className="px-5 py-4 text-neutral-600">
                    Single inquiry
                  </td>
                  <td className="px-5 py-4 text-neutral-500">
                    May involve multiple inquiries
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* SECTION 5: Payment Options */}
      <section className="bg-white px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[960px]">
          <h2 className="text-center text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            Payment options
          </h2>
          <p className="mt-3 text-center text-base md:text-lg text-neutral-500">
            We keep things flexible so you can pay the way that works for you.
          </p>

          <div className="mt-10 grid grid-cols-1 sm:grid-cols-3 gap-6">
            <div className="rounded-2xl border border-neutral-50 p-6 md:p-8 text-center">
              <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-primary/10">
                <svg
                  className="size-6 text-primary"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <rect
                    x="3"
                    y="6"
                    width="18"
                    height="13"
                    rx="2"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                  <circle
                    cx="12"
                    cy="12.5"
                    r="3"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                </svg>
              </div>
              <h3 className="mt-4 text-lg font-semibold text-neutral-700">
                Cash
              </h3>
              <p className="mt-2 text-sm text-neutral-500">
                Pay in full and drive away the same day.
              </p>
            </div>

            <div className="rounded-2xl border border-neutral-50 p-6 md:p-8 text-center">
              <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-primary/10">
                <svg
                  className="size-6 text-primary"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <rect
                    x="2"
                    y="5"
                    width="20"
                    height="14"
                    rx="2"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                  <path
                    d="M2 10h20"
                    stroke="currentColor"
                    strokeWidth="1.5"
                  />
                </svg>
              </div>
              <h3 className="mt-4 text-lg font-semibold text-neutral-700">
                Credit card
              </h3>
              <p className="mt-2 text-sm text-neutral-500">
                We accept major credit cards for your convenience.
              </p>
            </div>

            <div className="rounded-2xl border border-neutral-50 p-6 md:p-8 text-center">
              <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-primary/10">
                <svg
                  className="size-6 text-primary"
                  fill="none"
                  viewBox="0 0 24 24"
                >
                  <path
                    d="M8 17l-4-4m0 0l4-4m-4 4h12m4-4v8a2 2 0 01-2 2H6"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>
              <h3 className="mt-4 text-lg font-semibold text-neutral-700">
                Trade-in
              </h3>
              <p className="mt-2 text-sm text-neutral-500">
                Bring your current vehicle and apply its value toward your
                purchase.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* SECTION 6: Trust Signals */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[720px] text-center">
          <h2 className="text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            Why customers trust us
          </h2>
          <p className="mt-3 text-base md:text-lg text-neutral-500">
            We believe buying a car should feel straightforward, not stressful.
          </p>

          <div className="mt-10 grid grid-cols-1 sm:grid-cols-2 gap-5">
            {[
              {
                title: "Clean titles on every vehicle",
                desc: "No salvage, no flood damage, no surprises.",
              },
              {
                title: "Transparent process",
                desc: "We walk you through every step so there are no hidden fees.",
              },
              {
                title: "Local Texas business",
                desc: "We live here, we work here, and our reputation matters to us.",
              },
              {
                title: "Support throughout financing",
                desc: "Questions after the sale? We are still here to help.",
              },
            ].map((signal) => (
              <div
                key={signal.title}
                className="flex items-start gap-3 rounded-2xl border border-neutral-100 bg-white p-5 md:p-6 text-left"
              >
                <CheckIcon />
                <div>
                  <h3 className="text-base font-semibold text-neutral-700">
                    {signal.title}
                  </h3>
                  <p className="mt-1 text-sm text-neutral-500">
                    {signal.desc}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* SECTION 7: FAQ */}
      <section className="bg-white px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[720px]">
          <h2 className="text-center text-3xl md:text-4xl font-semibold tracking-tight text-neutral-700">
            Frequently asked questions
          </h2>

          <div className="mt-10 flex flex-col gap-5">
            {faqs.map((faq) => (
              <div
                key={faq.question}
                className="rounded-2xl border border-neutral-50 p-5 md:p-6"
              >
                <h3 className="text-base font-semibold text-neutral-700">
                  {faq.question}
                </h3>
                <p className="mt-2 text-sm md:text-base text-neutral-500 leading-relaxed">
                  {faq.answer}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* SECTION 8: Contact & Location */}
      <section className="bg-neutral-800 px-5 md:px-10 lg:px-20 py-14 md:py-20">
        <div className="mx-auto max-w-[960px]">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-10 md:gap-16">
            <div>
              <p className="text-xs font-semibold uppercase tracking-widest text-primary mb-3">
                Visit us
              </p>
              <h2 className="text-3xl md:text-4xl font-semibold tracking-tight text-white">
                Ready to get started?
              </h2>
              <p className="mt-4 text-base text-neutral-400 leading-relaxed">
                Stop by, give us a call, or browse our inventory online. We are
                here to make the process easy.
              </p>

              <div className="mt-8 flex flex-col sm:flex-row gap-4">
                <Link
                  href="/shop"
                  className="w-full sm:w-auto rounded-xl bg-primary px-8 py-3.5 text-center text-base font-semibold text-white hover:bg-primary-dark transition-colors"
                >
                  Browse inventory
                </Link>
                <a
                  href={callHref}
                  className="w-full sm:w-auto rounded-xl border border-neutral-600 px-8 py-3.5 text-center text-base font-semibold text-neutral-100 hover:border-neutral-400 transition-colors"
                >
                  {nap.phone ? `Call ${nap.phoneDisplay}` : "Contact us"}
                </a>
              </div>
            </div>

            <div className="flex flex-col gap-5 text-neutral-300">
              <div>
                <p className="text-sm text-neutral-500">Dealership</p>
                <p className="mt-1 text-base font-medium text-neutral-100">
                  {nap.name}
                </p>
              </div>
              {nap.hasAddress && (
                <div>
                  <p className="text-sm text-neutral-500">Address</p>
                  <p className="mt-1 text-base text-neutral-200">
                    {nap.addressLines.join(", ")}
                  </p>
                </div>
              )}
              <div>
                <p className="text-sm text-neutral-500">Phone</p>
                {nap.telHref ? (
                  <a
                    href={nap.telHref}
                    className="mt-1 block text-base font-medium text-neutral-100 hover:text-primary transition-colors"
                  >
                    {nap.phoneDisplay}
                  </a>
                ) : (
                  <p className="mt-1 text-base font-medium text-neutral-100">
                    {nap.phoneDisplay}
                  </p>
                )}
              </div>
              <div>
                <p className="text-sm text-neutral-500">Hours</p>
                <div className="mt-1 text-base text-neutral-200">
                  <p>{nap.hoursText}</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
}
