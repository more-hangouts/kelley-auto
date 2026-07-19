import type { Metadata } from "next";
import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import { resolveNap } from "@/lib/nap";

export const metadata: Metadata = {
  title: "Kelley Autoplex San Antonio TX | Privacy Policy",
  description:
    "Read the Kelley Autoplex privacy policy: what information we collect, how we use it, how we handle text messaging (SMS) and consent, and your choices.",
  alternates: {
    canonical: "/privacy-policy",
  },
};

const updated = "July 7, 2026";

export default async function PrivacyPolicyPage() {
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
            Privacy Policy
          </h1>
          <p className="mt-3 max-w-2xl text-base md:text-lg text-neutral-500">
            How we collect, use, and protect your information, including how we
            handle phone calls and text messages.
          </p>
        </section>

        <section className="px-5 md:px-10 lg:px-20 py-10 md:py-14 lg:py-16">
          <div className="max-w-4xl space-y-9 text-sm leading-7 text-neutral-600">
            <p className="text-neutral-400">Last updated: {updated}</p>

            <section>
              <p>
                This Privacy Policy explains how {nap.legalName} (&ldquo;Kelley
                Autoplex,&rdquo; &ldquo;we,&rdquo; &ldquo;us,&rdquo; or
                &ldquo;our&rdquo;) collects, uses, and shares information when
                you visit our website, submit a form, or communicate with us by
                phone, text message, or email. By using this website or
                contacting us, you agree to this Privacy Policy.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Information We Collect</h2>
              <p className="mt-3">
                We collect information you provide directly to us, such as your
                name, phone number, email address, mailing address, the vehicles
                or services you ask about, and any details you include when you
                submit a contact, appointment, financing, or vehicle-inquiry
                form. If you pursue financing, we may collect additional
                information needed to process your request.
              </p>
              <p className="mt-3">
                We also automatically collect certain technical information when
                you visit the website, such as your device type, browser, pages
                viewed, and referring links, using cookies and similar
                technologies for analytics and to improve the site.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">How We Use Your Information</h2>
              <p className="mt-3">
                We use the information we collect to respond to your inquiries,
                schedule appointments, provide vehicle and pricing details,
                assist with financing, follow up on your interest, operate and
                improve our website, and comply with legal obligations. When you
                submit a form, you authorize us to contact you about your
                inquiry by phone, text message, and email using the contact
                information you provide.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Text Messaging (SMS) and Consent</h2>
              <p className="mt-3">
                Text-message consent is collected only through the optional
                consent checkbox on our website forms, which is unchecked by
                default. If you check it, you agree to receive calls and text
                messages from Kelley Autoplex about your inquiry, including
                responses, appointment coordination, vehicle availability, and
                related follow-up. Consent is not a condition of any purchase
                or service — you can submit any form, receive a response, and
                do business with us without opting in to text messages. Message
                frequency varies. Message and data rates may apply.
              </p>
              <p className="mt-3">
                You can opt out of text messages at any time by replying{" "}
                <strong>STOP</strong> to any message. After you reply STOP, we
                will stop sending text messages to that number, and you may
                receive a final confirmation. Reply <strong>HELP</strong> for
                help, or contact us using the details below. Opting out of text
                messages does not remove you from phone or email contact unless
                you also ask us to stop those.
              </p>
              <p className="mt-3">
                <strong>
                  Mobile information will not be shared with third parties or
                  affiliates for marketing or promotional purposes.
                </strong>{" "}
                Text-messaging originator opt-in data and consent are not shared
                with any third parties. All other categories of information
                remain subject to the sharing described in this policy.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">How We Share Information</h2>
              <p className="mt-3">
                We do not sell your personal information. We may share
                information with service providers who help us operate our
                business and website (for example, messaging, hosting, and
                analytics providers) and, if you request financing, with banks,
                credit unions, or lenders needed to process your request. We may
                also share information when required by law or to protect our
                rights. As noted above, text-messaging consent and phone numbers
                collected for SMS are never shared with third parties for
                marketing.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Cookies and Analytics</h2>
              <p className="mt-3">
                We use cookies and similar technologies to understand how the
                website is used, remember your preferences, and measure the
                performance of our marketing. You can control cookies through
                your browser settings. Some features of the website may not work
                properly if cookies are disabled.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Data Security and Retention</h2>
              <p className="mt-3">
                We take reasonable administrative, technical, and physical
                measures to protect the information we collect. No method of
                transmission or storage is completely secure, so we cannot
                guarantee absolute security. We retain information for as long as
                needed to serve you, run our business, and meet legal
                requirements.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Your Choices</h2>
              <p className="mt-3">
                You may ask us to update or delete your information, or to stop
                contacting you by text, phone, or email, by using the contact
                details below. To stop text messages, reply STOP to any message.
                You may also control cookies through your browser.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Children&rsquo;s Privacy</h2>
              <p className="mt-3">
                This website is intended for adults. We do not knowingly collect
                personal information from children under 13. If you believe a
                child has provided us information, please contact us so we can
                remove it.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Changes to This Policy</h2>
              <p className="mt-3">
                We may update this Privacy Policy from time to time by posting a
                revised version on this page and updating the date above. Your
                continued use of the website after changes are posted means you
                accept the updated policy.
              </p>
            </section>

            <section>
              <h2 className="text-xl font-semibold text-neutral-700">Contact</h2>
              <p className="mt-3">
                Questions about this Privacy Policy or your information may be
                directed to {nap.name}
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
