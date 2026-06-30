import type { Metadata } from "next";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import { resolveNap } from "@/lib/nap";

export const metadata: Metadata = {
  title: "Kelley Autoplex San Antonio TX | Terms and Conditions",
  description:
    "Read the Kelley Autoplex website terms and conditions for inventory information, appointments, financing resources, third-party links, and site use.",
  alternates: {
    canonical: "/terms-and-conditions",
  },
};

const updated = "June 30, 2026";

export default async function TermsAndConditionsPage() {
  const nap = await resolveNap();

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      <main>
        <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-10 md:py-14">
          <p className="text-sm font-medium text-primary uppercase tracking-wide">
            Kelley Autoplex
          </p>
          <h1 className="mt-2 text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-neutral-700">
            Terms and Conditions
          </h1>
          <p className="mt-3 max-w-2xl text-base md:text-lg text-neutral-500">
            Please read these terms before using this website or submitting a request.
          </p>
        </section>

        <section className="px-5 md:px-10 lg:px-20 py-10 md:py-14 lg:py-16">
          <div className="max-w-4xl space-y-9 text-sm leading-7 text-neutral-600">
            <p className="text-neutral-400">Last updated: {updated}</p>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Acceptance of Terms</h2>
              <p className="mt-3">
                By accessing or using this website, you agree to these Terms and Conditions.
                If you do not agree, please do not use the website. Kelley Autoplex may update
                these terms from time to time by posting a revised version on this page.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Website Information</h2>
              <p className="mt-3">
                We work to keep website information accurate, including vehicle descriptions,
                photos, mileage, prices, availability, hours, and contact information. Inventory
                changes often, and errors or delays may occur. Website content is provided for
                general informational purposes and is not a binding offer to sell a vehicle.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Vehicle Availability and Pricing</h2>
              <p className="mt-3">
                All vehicles are subject to prior sale, price change, correction, or withdrawal
                without notice. Prices, mileage, features, photos, and vehicle condition should
                be confirmed directly with Kelley Autoplex before you rely on them or schedule
                a visit. Taxes, title, registration, inspection, dealer fees, and other government
                fees may not be included unless stated otherwise.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Appointments and Lead Forms</h2>
              <p className="mt-3">
                When you submit a contact, appointment, or vehicle inquiry form, you authorize
                Kelley Autoplex to contact you using the information you provide. Submitting a
                form does not reserve a vehicle, guarantee availability, guarantee approval, or
                create a purchase agreement.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Financing Information</h2>
              <p className="mt-3">
                Financing content on this website is provided as a convenience. Kelley Autoplex
                may reference banks, credit unions, or third-party lenders, but approval, rates,
                down payments, terms, and eligibility are determined by the lender. Kelley
                Autoplex does not guarantee financing approval or any specific loan terms.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Third-Party Websites</h2>
              <p className="mt-3">
                This website may link to third-party websites or services, including lenders,
                vehicle history providers, maps, analytics, or other resources. Those websites
                are controlled by third parties, and Kelley Autoplex is not responsible for their
                content, policies, security, or availability.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Permitted Use</h2>
              <p className="mt-3">
                You may use this website for lawful personal shopping and informational purposes.
                You agree not to misuse the website, interfere with its operation, attempt
                unauthorized access, scrape or copy content at scale, submit false information,
                or use the website in a way that violates applicable law.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">No Warranties</h2>
              <p className="mt-3">
                This website is provided on an “as available” basis. Kelley Autoplex does not
                warrant that the website will be uninterrupted, error-free, secure, or free from
                harmful components. To the fullest extent allowed by law, website content is
                provided without warranties of any kind.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Limitation of Liability</h2>
              <p className="mt-3">
                To the fullest extent allowed by law, Kelley Autoplex will not be liable for
                indirect, incidental, consequential, special, or punitive damages arising from
                your use of this website or reliance on website information.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Contact</h2>
              <p className="mt-3">
                Questions about these terms may be directed to {nap.name}
                {nap.email ? (
                  <>
                    {" "}at <a className="font-medium text-primary underline" href={`mailto:${nap.email}`}>{nap.email}</a>
                  </>
                ) : null}
                {nap.phone && nap.telHref ? (
                  <>
                    {" "}or <a className="font-medium text-primary underline" href={nap.telHref}>{nap.phoneDisplay}</a>
                  </>
                ) : null}
                .
              </p>
              {nap.hasAddress && (
                <p className="mt-2">
                  Mailing address: {nap.addressLines.join(", ")}.
                </p>
              )}
            </section>
          </div>
        </section>
      </main>

      <Footer />
    </div>
  );
}
