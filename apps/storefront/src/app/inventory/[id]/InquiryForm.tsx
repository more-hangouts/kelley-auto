"use client";

import { useState, useEffect, useRef } from "react";
import { submitLead } from "@/lib/publicApi";
import { getTrackingContext, track } from "@/lib/analytics";
import { fbqTrack, vehicleContentParams } from "@/lib/metaPixel";
import { isValidDateOfBirth, maskDateOfBirth } from "@/lib/dob";

const FORM_TYPE = "vehicle_inquiry";

// — time slot helpers —
const ALL_SLOTS = [10, 11, 12, 13, 14, 15, 16, 17];
const US_STATES = [
  "Alabama",
  "Alaska",
  "Arizona",
  "Arkansas",
  "California",
  "Colorado",
  "Connecticut",
  "Delaware",
  "District of Columbia",
  "Florida",
  "Georgia",
  "Hawaii",
  "Idaho",
  "Illinois",
  "Indiana",
  "Iowa",
  "Kansas",
  "Kentucky",
  "Louisiana",
  "Maine",
  "Maryland",
  "Massachusetts",
  "Michigan",
  "Minnesota",
  "Mississippi",
  "Missouri",
  "Montana",
  "Nebraska",
  "Nevada",
  "New Hampshire",
  "New Jersey",
  "New Mexico",
  "New York",
  "North Carolina",
  "North Dakota",
  "Ohio",
  "Oklahoma",
  "Oregon",
  "Pennsylvania",
  "Rhode Island",
  "South Carolina",
  "South Dakota",
  "Tennessee",
  "Texas",
  "Utah",
  "Vermont",
  "Virginia",
  "Washington",
  "West Virginia",
  "Wisconsin",
  "Wyoming",
];

function getSlots(): number[] {
  const h = new Date().getHours();
  return h >= 17 ? ALL_SLOTS.filter((s) => s >= 13) : ALL_SLOTS;
}

function fmtSlot(h: number): string {
  if (h === 12) return "12:00 PM";
  return h > 12 ? `${h - 12}:00 PM` : `${h}:00 AM`;
}

function getTomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return d.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

// Tomorrow as a plain YYYY-MM-DD (dealership-local calendar date). Sent to the
// backend with the chosen hour so the requested slot becomes an appointment.
function getTomorrowISO(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// — smooth slide-in wrapper (mounts hidden, transitions to visible on next frame) —
function Slide({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setReady(true), 16);
    return () => clearTimeout(t);
  }, []);
  return (
    <div
      className={`transition-all duration-300 ease-out ${
        ready ? "opacity-100 translate-y-0" : "opacity-0 translate-y-2"
      }`}
    >
      {children}
    </div>
  );
}

// — compact confirmed step row —
function ConfirmedRow({
  label,
  onEdit,
}: {
  label: string;
  onEdit: () => void;
}) {
  return (
    <div className="flex items-center justify-between py-1">
      <div className="flex items-center gap-2 min-w-0">
        <span className="flex shrink-0 size-5 items-center justify-center rounded-full bg-primary/15">
          <svg className="size-3 text-primary" fill="none" viewBox="0 0 12 12">
            <path
              d="M2 6l3 3 5-5"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="text-sm text-neutral-600 truncate">{label}</span>
      </div>
      <button
        type="button"
        onClick={onEdit}
        className="shrink-0 ml-2 text-xs text-neutral-400 hover:text-primary transition-colors underline"
      >
        edit
      </button>
    </div>
  );
}

// — continue button —
function ContinueBtn({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="self-end flex items-center gap-1.5 rounded-xl bg-neutral-700 disabled:bg-neutral-100 px-4 py-2.5 text-sm font-semibold text-white disabled:text-neutral-400 transition-all"
    >
      Continue
      <svg className="size-4" fill="none" viewBox="0 0 16 16">
        <path
          d="M3.5 8h9M8.5 4l4 4-4 4"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

const inputClass =
  "w-full rounded-xl border border-neutral-100 bg-white px-4 py-3 text-sm text-neutral-700 placeholder-neutral-400 outline-none transition-colors focus:border-primary";

export default function InquiryForm({
  vehicleId,
  vehicleTitle,
  telHref,
}: {
  vehicleId: string;
  vehicleTitle: string;
  /** tel: href from the business profile NAP; null when no phone is set. */
  telHref?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0); // 0=name, 1=email, 2=phone, 3=approval, 4=time
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [address, setAddress] = useState("");
  const [dateOfBirth, setDateOfBirth] = useState("");
  const [hasDriversLicense, setHasDriversLicense] = useState(false);
  const [driversLicenseState, setDriversLicenseState] = useState("");
  const [slot, setSlot] = useState<number | null>(null);
  // A2P 10DLC: SMS consent — never pre-checked and never required to submit
  // (consent may not be a condition of service). Recorded server-side.
  const [smsConsent, setSmsConsent] = useState(false);

  // Analytics: opened when the customer expands the form; started once they
  // advance past the first step. Both fire at most once per mount.
  const openedRef = useRef(false);
  useEffect(() => {
    if (open && !openedRef.current) {
      openedRef.current = true;
      track("lead_form_opened", {
        vehicle: { vehicleId },
        metadata: { form_type: FORM_TYPE },
      });
    }
  }, [open, vehicleId]);
  const startedRef = useRef(false);
  useEffect(() => {
    if (step > 0 && !startedRef.current) {
      startedRef.current = true;
      track("lead_form_started", {
        vehicle: { vehicleId },
        metadata: { form_type: FORM_TYPE },
      });
    }
  }, [step, vehicleId]);

  function close() {
    setOpen(false);
    setStep(0);
  }

  async function submit() {
    if (!slot) return;
    setLoading(true);
    setError(null);
    // No canned "message": every storefront lead is BHPH/no-credit-check, so
    // boilerplate is identical for everyone and just clutters the deal notes.
    // The vehicle is captured structurally (vehicleId) and the requested time
    // below becomes a real pending appointment — not a note. PII stays in the
    // discrete encrypted fields, never folded into a message.
    // Captured once so the browser Pixel `Lead` below fires with the SAME
    // event_id the backend sends via CAPI — Meta dedups the pair.
    const tracking = getTrackingContext();
    const result = await submitLead({
      name: `${firstName} ${lastName}`.trim(),
      email,
      phone,
      vehicleId,
      preferredTime: `${fmtSlot(slot)} on ${getTomorrow()}`,
      preferredDate: getTomorrowISO(),
      preferredHour: slot,
      sourcePage:
        typeof window !== "undefined" ? window.location.pathname : undefined,
      addressStreet: address.trim() || undefined,
      dateOfBirth: dateOfBirth.trim() || undefined,
      hasDriverLicense: hasDriversLicense,
      driverLicenseState:
        driversLicenseState.trim().toUpperCase() || undefined,
      smsConsent,
      // Attribution: ties this lead to the visitor's browsing journey.
      tracking,
    });
    if (result.ok) {
      setSent(true);
      fbqTrack("Lead", vehicleContentParams({ vehicleId }), tracking.event_id);
    } else {
      setError(result.message || "Something went wrong. Please call us.");
    }
    setLoading(false);
  }

  if (sent) {
    return (
      <div className="rounded-2xl bg-green-50 p-6 text-center">
        <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-full bg-green-100">
          <svg className="size-6 text-green-600" fill="none" viewBox="0 0 24 24">
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p className="text-base font-semibold text-green-800">Request sent!</p>
        <p className="mt-1 text-sm text-green-600">
          We&apos;ll confirm your appointment for the {vehicleTitle} shortly.
        </p>
      </div>
    );
  }

  const slots = getSlots();
  const nameValid = firstName.trim().length > 0 && lastName.trim().length > 0;
  const emailValid = email.includes("@") && email.includes(".");
  const phoneValid = phone.trim().length > 0;
  const approvalValid =
    address.trim().length > 0 &&
    isValidDateOfBirth(dateOfBirth) &&
    hasDriversLicense &&
    US_STATES.some(
      (state) =>
        state.toLowerCase() === driversLicenseState.trim().toLowerCase()
    );
  const approvalSummary = [
    address.trim(),
    dateOfBirth,
    driversLicenseState.trim().toUpperCase(),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div>
      {!open ? (
        <div className="flex gap-3">
          <button
            onClick={() => setOpen(true)}
            className="flex-1 rounded-xl bg-gradient-to-b from-[#f9896a] to-primary py-3.5 text-base font-semibold text-white transition-opacity hover:opacity-95"
          >
            Get Approved · No Credit Check
          </button>
          <a
            href={telHref ?? undefined}
            className="flex items-center gap-2 rounded-xl border border-neutral-100 bg-white px-5 py-3.5 text-sm font-semibold text-neutral-700 transition-colors hover:bg-neutral-25"
          >
            <svg className="size-4" fill="none" viewBox="0 0 20 20">
              <path
                d="M4.5 3.5c-.5 0-1 .3-1.3.8L2 6.5C2 12.3 7.7 18 13.5 18l2.2-1.2c.5-.3.8-.8.8-1.3v-2.2c0-.6-.4-1-.9-1.1l-2.5-.5c-.5-.1-1 .1-1.3.5l-.7 1c-1.3-.7-2.5-1.9-3.2-3.2l1-.7c.4-.3.6-.8.5-1.3L8.8 4.4c-.1-.5-.5-.9-1.1-.9H4.5z"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Call
          </a>
        </div>
      ) : (
        <div className="rounded-2xl border border-neutral-100 overflow-hidden">
          {/* Widget header */}
          <div className="flex items-center justify-between bg-neutral-25 px-4 py-3 border-b border-neutral-100">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-neutral-700">
                Get approved — no credit check
              </p>
              <p className="text-xs text-neutral-400 mt-0.5 truncate">
                {vehicleTitle}
              </p>
            </div>
            <button
              type="button"
              onClick={close}
              className="ml-3 shrink-0 flex size-7 items-center justify-center rounded-lg text-neutral-400 hover:text-neutral-700 hover:bg-neutral-100 transition-colors"
            >
              <svg className="size-4" fill="none" viewBox="0 0 16 16">
                <path
                  d="M12 4L4 12M4 4l8 8"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>

          {/* Widget body */}
          <div className="px-4 pt-3 pb-4 flex flex-col gap-1">
            {/* Confirmed rows */}
            {step > 0 && (
              <ConfirmedRow
                label={`${firstName} ${lastName}`}
                onEdit={() => setStep(0)}
              />
            )}
            {step > 1 && (
              <ConfirmedRow label={email} onEdit={() => setStep(1)} />
            )}
            {step > 2 && (
              <ConfirmedRow label={phone} onEdit={() => setStep(2)} />
            )}
            {step > 3 && (
              <ConfirmedRow
                label={approvalSummary || "Approval details"}
                onEdit={() => setStep(3)}
              />
            )}

            {step > 0 && <div className="border-t border-neutral-50 my-2" />}

            {/* Active step — key forces remount on step change, triggering Slide animation */}
            <Slide key={step}>
              {step === 0 && (
                <div className="flex flex-col gap-3">
                  <p className="text-sm font-medium text-neutral-500">
                    What&apos;s your name?
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      autoFocus
                      placeholder="First name"
                      value={firstName}
                      onChange={(e) => setFirstName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && nameValid) setStep(1);
                      }}
                      className={inputClass}
                    />
                    <input
                      placeholder="Last name"
                      value={lastName}
                      onChange={(e) => setLastName(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && nameValid) setStep(1);
                      }}
                      className={inputClass}
                    />
                  </div>
                  <ContinueBtn
                    disabled={!nameValid}
                    onClick={() => setStep(1)}
                  />
                </div>
              )}

              {step === 1 && (
                <div className="flex flex-col gap-3">
                  <p className="text-sm font-medium text-neutral-500">
                    Your email address?
                  </p>
                  <input
                    autoFocus
                    type="email"
                    placeholder="email@example.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && emailValid) setStep(2);
                    }}
                    className={inputClass}
                  />
                  <ContinueBtn
                    disabled={!emailValid}
                    onClick={() => setStep(2)}
                  />
                </div>
              )}

              {step === 2 && (
                <div className="flex flex-col gap-3">
                  <p className="text-sm font-medium text-neutral-500">
                    Your phone number?
                  </p>
                  <input
                    autoFocus
                    type="tel"
                    placeholder="(555) 000-0000"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && phoneValid) setStep(3);
                    }}
                    className={inputClass}
                  />
                  <ContinueBtn
                    disabled={!phoneValid}
                    onClick={() => setStep(3)}
                  />
                </div>
              )}

              {step === 3 && (
                <div className="flex flex-col gap-3">
                  <div>
                    <p className="text-sm font-medium text-neutral-500">
                      Get approved in minutes
                    </p>
                    <p className="text-xs text-neutral-400 mt-0.5">
                      No credit check approval.
                    </p>
                  </div>
                  <input
                    autoFocus
                    placeholder="Address"
                    value={address}
                    onChange={(e) => setAddress(e.target.value)}
                    className={inputClass}
                  />
                  <input
                    type="text"
                    inputMode="numeric"
                    aria-label="DOB or date of birth"
                    placeholder="DOB / Date of birth (mm/dd/yyyy)"
                    value={dateOfBirth}
                    onChange={(e) =>
                      setDateOfBirth(maskDateOfBirth(e.target.value))
                    }
                    maxLength={10}
                    className={inputClass}
                  />
                  {dateOfBirth.length === 10 &&
                    !isValidDateOfBirth(dateOfBirth) && (
                      <p className="-mt-1 text-xs text-red-600">
                        That date doesn&apos;t look right — please check it.
                      </p>
                    )}
                  <label className="flex items-start gap-3 rounded-xl border border-neutral-100 bg-neutral-25 px-4 py-3 text-sm text-neutral-600">
                    <input
                      type="checkbox"
                      checked={hasDriversLicense}
                      onChange={(e) => setHasDriversLicense(e.target.checked)}
                      className="mt-0.5 size-4 accent-primary"
                    />
                    <span>Yes, I have a driver&apos;s license</span>
                  </label>
                  <input
                    list="drivers-license-states"
                    placeholder="Driver's license state"
                    value={driversLicenseState}
                    onChange={(e) => setDriversLicenseState(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && approvalValid) setStep(4);
                    }}
                    className={inputClass}
                  />
                  <datalist id="drivers-license-states">
                    {US_STATES.map((state) => (
                      <option key={state} value={state} />
                    ))}
                  </datalist>
                  <ContinueBtn
                    disabled={!approvalValid}
                    onClick={() => setStep(4)}
                  />
                </div>
              )}

              {step === 4 && (
                <div className="flex flex-col gap-3">
                  <div>
                    <p className="text-sm font-medium text-neutral-500">
                      Pick a time
                    </p>
                    <p className="text-xs text-neutral-400 mt-0.5">
                      {getTomorrow()}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {slots.map((h) => (
                      <button
                        key={h}
                        type="button"
                        onClick={() => setSlot(h)}
                        className={`rounded-xl border px-3.5 py-2 text-sm font-medium transition-colors ${
                          slot === h
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-neutral-100 bg-neutral-25 text-neutral-600 hover:border-primary/30 hover:bg-white"
                        }`}
                      >
                        {fmtSlot(h)}
                      </button>
                    ))}
                  </div>
                  <label className="flex items-start gap-3 text-xs leading-5 text-neutral-500">
                    <input
                      type="checkbox"
                      checked={smsConsent}
                      onChange={(e) => setSmsConsent(e.target.checked)}
                      className="mt-0.5 size-4 shrink-0 accent-primary"
                    />
                    <span>
                      Optional: By checking this box, I agree to receive calls
                      and text messages from Kelley Autoplex about my inquiry at
                      the phone number provided, including via automated
                      technology. Consent is not a condition of any purchase or
                      service — you may submit this form without checking this
                      box. Msg frequency varies. Msg &amp; data rates may apply.
                      Reply STOP to opt out, HELP for help. See our{" "}
                      <a
                        href="/privacy-policy"
                        target="_blank"
                        className="font-medium text-primary underline"
                      >
                        Privacy Policy
                      </a>{" "}
                      and{" "}
                      <a
                        href="/terms-and-conditions"
                        target="_blank"
                        className="font-medium text-primary underline"
                      >
                        Terms
                      </a>
                      .
                    </span>
                  </label>
                  {error && (
                    <p className="text-xs text-red-500">{error}</p>
                  )}
                  <button
                    type="button"
                    disabled={!slot || loading}
                    onClick={submit}
                    className="w-full rounded-xl bg-gradient-to-b from-[#f9896a] to-primary py-3.5 text-sm font-semibold text-white transition-opacity hover:opacity-95 disabled:opacity-40"
                  >
                    {loading ? "Sending..." : "Get Approved · No Credit Check"}
                  </button>
                </div>
              )}
            </Slide>
          </div>
        </div>
      )}
    </div>
  );
}
