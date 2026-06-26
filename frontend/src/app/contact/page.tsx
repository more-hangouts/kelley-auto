import TopBanner from "../components/TopBanner";
import NavbarWrapper from "../components/NavbarWrapper";
import Footer from "../components/Footer";
import ContactForm from "./ContactForm";
import { getContactPage } from "@/lib/api";
import { resolveNap } from "@/lib/nap";
import { lexicalToHtml } from "@/lib/richtext-html";

export const metadata = {
  title: "Contact Us | Kelley Autoplex",
  description:
    "Browse current inventory, ask about a vehicle, or schedule a visit. Contact us to confirm availability.",
};

export const revalidate = 60;

export default async function ContactPage() {
  const [nap, page] = await Promise.all([resolveNap(), getContactPage()]);

  const { phoneDisplay: phone, telHref, email } = nap;

  const tagline = page.tagline || "Get in Touch";
  const heading = page.heading || "Contact Us";
  const formHeading = page.formHeading || "Request an Appointment";

  const descriptionHtml = lexicalToHtml(page.description) ||
    "<p>All vehicle viewings are <strong><em>by appointment only</em></strong>. Pick a time that works for you and we&apos;ll confirm same day.</p>";
  const appointmentNoteHtml = lexicalToHtml(page.appointmentNote) ||
    "<p>All visits are by appointment only — walk-ins are not accepted.</p>";

  return (
    <div className="min-h-screen">
      <TopBanner />
      <NavbarWrapper />

      {/* Header */}
      <section className="bg-neutral-25 px-5 md:px-10 lg:px-20 py-10 md:py-14">
        <p className="text-sm font-medium text-primary uppercase tracking-wide">{tagline}</p>
        <h1 className="mt-2 text-3xl md:text-4xl lg:text-5xl font-semibold tracking-tight text-neutral-700">
          {heading}
        </h1>
        <div
          className="mt-3 max-w-xl text-base md:text-lg text-neutral-500 [&_strong]:font-semibold [&_em]:italic [&_a]:text-primary [&_a]:underline"
          dangerouslySetInnerHTML={{ __html: descriptionHtml }}
        />
      </section>

      {/* Content */}
      <section className="px-5 md:px-10 lg:px-20 py-10 md:py-14 lg:py-16">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 lg:gap-16">

          {/* Left — contact info */}
          <div className="flex flex-col gap-8">

            {/* Hours */}
            <div>
              <h2 className="text-lg font-semibold text-neutral-700">Hours &amp; Availability</h2>
              {nap.hours ? (
                <div className="mt-3 flex flex-col gap-2 text-sm text-neutral-600">
                  {nap.hours.days.map((d) => (
                    <div
                      key={d.day}
                      className="flex justify-between border-b border-neutral-50 pb-2 last:border-0"
                    >
                      <span>{d.day}</span>
                      <span
                        className={`font-medium ${d.closed ? "text-neutral-400" : "text-neutral-700"}`}
                      >
                        {d.display}
                      </span>
                    </div>
                  ))}
                  {nap.hours.timezone && (
                    <p className="pt-1 text-xs text-neutral-400">
                      Times shown in {nap.hours.timezone}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-sm text-neutral-600">{nap.hoursText}</p>
              )}
              <div
                className="mt-3 rounded-xl bg-neutral-25 border border-neutral-50 px-4 py-3 text-sm text-neutral-500 [&_strong]:font-semibold [&_em]:italic"
                dangerouslySetInnerHTML={{ __html: appointmentNoteHtml }}
              />
            </div>

            {/* Contact details */}
            <div>
              <h2 className="text-lg font-semibold text-neutral-700">Reach Us Directly</h2>
              <div className="mt-3 flex flex-col gap-4">
                {(() => {
                  const phoneInner = (
                    <>
                      <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10">
                        <svg className="size-5 text-primary" fill="none" viewBox="0 0 20 20">
                          <path
                            d="M4.5 3.5c-.5 0-1 .3-1.3.8L2 6.5C2 12.3 7.7 18 13.5 18l2.2-1.2c.5-.3.8-.8.8-1.3v-2.2c0-.6-.4-1-.9-1.1l-2.5-.5c-.5-.1-1 .1-1.3.5l-.7 1c-1.3-.7-2.5-1.9-3.2-3.2l1-.7c.4-.3.6-.8.5-1.3L8.8 4.4c-.1-.5-.5-.9-1.1-.9H4.5z"
                            stroke="currentColor"
                            strokeWidth="1.4"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </div>
                      <div>
                        <p className="text-xs text-neutral-400">Phone</p>
                        <p className="text-sm font-medium text-neutral-700 group-hover:text-primary transition-colors">
                          {phone}
                        </p>
                      </div>
                    </>
                  );
                  return telHref ? (
                    <a href={telHref} className="flex items-center gap-3 group">
                      {phoneInner}
                    </a>
                  ) : (
                    <div className="flex items-center gap-3">{phoneInner}</div>
                  );
                })()}

                {email && (
                  <a href={`mailto:${email}`} className="flex items-center gap-3 group">
                    <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10">
                      <svg className="size-5 text-primary" fill="none" viewBox="0 0 20 20">
                        <path d="M2.5 5.5A1.5 1.5 0 0 1 4 4h12a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 16 16H4a1.5 1.5 0 0 1-1.5-1.5v-9Z" stroke="currentColor" strokeWidth="1.4" />
                        <path d="m2.5 5.5 7.5 5.5 7.5-5.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-xs text-neutral-400">Email</p>
                      <p className="text-sm font-medium text-neutral-700 group-hover:text-primary transition-colors">
                        {email}
                      </p>
                    </div>
                  </a>
                )}

                {nap.hasAddress && (
                  <div className="flex items-center gap-3">
                    <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10">
                      <svg className="size-5 text-primary" fill="none" viewBox="0 0 20 20">
                        <path d="M10 2a6 6 0 0 1 6 6c0 4-6 10-6 10S4 12 4 8a6 6 0 0 1 6-6Z" stroke="currentColor" strokeWidth="1.4" />
                        <circle cx="10" cy="8" r="2" stroke="currentColor" strokeWidth="1.4" />
                      </svg>
                    </div>
                    <div>
                      <p className="text-xs text-neutral-400">Address</p>
                      <p className="text-sm font-medium text-neutral-700">
                        {nap.addressLines.join(", ")}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Right — form */}
          <div>
            <h2 className="text-lg font-semibold text-neutral-700 mb-5">{formHeading}</h2>
            <ContactForm />
          </div>
        </div>
      </section>

      <Footer />
    </div>
  );
}
