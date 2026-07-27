"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { submitLead } from "@/lib/publicApi";
import { getTrackingContext, track } from "@/lib/analytics";
import { fbqTrack, vehicleContentParams } from "@/lib/metaPixel";
import { isValidDateOfBirth, maskDateOfBirth } from "@/lib/dob";

const FORM_TYPE = "loan_application";

export type VehicleInterestOption = {
  id: string;
  listingCode: string;
  label: string;
};

type FormState = {
  firstName: string;
  lastName: string;
  email: string;
  phone: string;
  interestedVehicle: string;
  address: string;
  dateOfBirth: string;
  hasDriversLicense: boolean;
  driversLicenseState: string;
  message: string;
};

const inputClass =
  "w-full rounded-xl border border-neutral-100 bg-white px-4 py-3 text-sm text-neutral-700 placeholder-neutral-400 outline-none transition-colors focus:border-primary";

function listingCodeFromInterest(value: string): string | null {
  const match = value.match(/\bKAP-\d{5}\b/i);
  return match ? match[0].toUpperCase() : null;
}

export default function LoanApplicationForm({
  vehicles,
}: {
  vehicles: VehicleInterestOption[];
}) {
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // A2P 10DLC: SMS consent — never pre-checked and never required to submit
  // (consent may not be a condition of service). Recorded server-side.
  const [smsConsent, setSmsConsent] = useState(false);
  const [form, setForm] = useState<FormState>({
    firstName: "",
    lastName: "",
    email: "",
    phone: "",
    interestedVehicle: "",
    address: "",
    dateOfBirth: "",
    hasDriversLicense: false,
    driversLicenseState: "",
    message: "",
  });

  const vehicleByLabel = useMemo(() => {
    const map = new Map<string, VehicleInterestOption>();
    for (const vehicle of vehicles) map.set(vehicle.label, vehicle);
    return map;
  }, [vehicles]);

  // Analytics: the customer opened the form, and (once) started filling it in.
  useEffect(() => {
    track("lead_form_opened", { metadata: { form_type: FORM_TYPE } });
  }, []);
  const startedRef = useRef(false);
  function markStarted() {
    if (startedRef.current) return;
    startedRef.current = true;
    track("lead_form_started", { metadata: { form_type: FORM_TYPE } });
  }

  function update(
    field: Exclude<keyof FormState, "hasDriversLicense">
  ): (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => void {
    return (e) => {
      markStarted();
      setForm((current) => ({ ...current, [field]: e.target.value }));
    };
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    // `required` only catches an empty field; a complete-but-impossible date
    // ("13/45/1995") would otherwise sail through to the encrypted column.
    if (!isValidDateOfBirth(form.dateOfBirth)) {
      setError("Please enter your date of birth as mm/dd/yyyy.");
      return;
    }
    setLoading(true);
    setError(null);

    const selectedVehicle = vehicleByLabel.get(form.interestedVehicle.trim());
    const listingCode =
      selectedVehicle?.listingCode ||
      listingCodeFromInterest(form.interestedVehicle);
    // Only genuine, customer-specific signal goes in the note. Boilerplate
    // ("standard approval / no credit check") is identical for every BHPH lead
    // — noise. When the typed vehicle resolves to a listing it's captured
    // structurally; when it doesn't, keep the customer's words so the interest
    // isn't lost. PII stays in the discrete encrypted fields, never here.
    const noteParts: string[] = [];
    if (!listingCode && form.interestedVehicle.trim()) {
      noteParts.push(`Interested in: ${form.interestedVehicle.trim()}`);
    }
    if (form.message.trim()) noteParts.push(form.message.trim());
    const message = noteParts.join("\n") || undefined;

    // Captured once so the browser Pixel `Lead` below fires with the SAME
    // event_id the backend sends via CAPI — Meta dedups the pair.
    const tracking = getTrackingContext();
    const result = await submitLead({
      name: `${form.firstName} ${form.lastName}`.trim(),
      email: form.email,
      phone: form.phone,
      listingCode,
      message,
      sourcePage:
        typeof window !== "undefined"
          ? window.location.pathname
          : "/loan-application",
      addressStreet: form.address.trim() || undefined,
      dateOfBirth: form.dateOfBirth.trim() || undefined,
      hasDriverLicense: form.hasDriversLicense,
      driverLicenseState:
        form.driversLicenseState.trim().toUpperCase() || undefined,
      smsConsent,
      // Attribution: ties this lead to the visitor's browsing journey. The
      // server records the authoritative `lead_submitted` event using
      // event_id (shared with the Meta Pixel/CAPI event below for dedup).
      tracking,
    });

    if (result.ok) {
      setSent(true);
      fbqTrack(
        "Lead",
        listingCode ? vehicleContentParams({ listingCode }) : {},
        tracking.event_id
      );
    } else {
      setError(result.message || "Something went wrong. Please call us.");
    }
    setLoading(false);
  }

  if (sent) {
    return (
      <div className="rounded-2xl bg-green-50 p-8 text-center">
        <div className="mx-auto mb-4 flex size-14 items-center justify-center rounded-full bg-green-100">
          <svg className="size-7 text-green-600" fill="none" viewBox="0 0 24 24">
            <path
              d="M5 13l4 4L19 7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </div>
        <p className="text-lg font-semibold text-green-800">Application received!</p>
        <p className="mt-1 text-sm text-green-600">
          We will review it and reach out shortly about your approval.
        </p>
      </div>
    );
  }

  return (
    <form id="application" onSubmit={handleSubmit} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <input
          required
          placeholder="First name"
          value={form.firstName}
          onChange={update("firstName")}
          className={inputClass}
        />
        <input
          required
          placeholder="Last name"
          value={form.lastName}
          onChange={update("lastName")}
          className={inputClass}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <input
          required
          type="email"
          placeholder="Email address"
          value={form.email}
          onChange={update("email")}
          className={inputClass}
        />
        <input
          required
          type="tel"
          placeholder="Phone number"
          value={form.phone}
          onChange={update("phone")}
          className={inputClass}
        />
      </div>

      <div>
        <input
          list="vehicle-interest-options"
          placeholder="Car you're interested in (optional)"
          value={form.interestedVehicle}
          onChange={update("interestedVehicle")}
          className={inputClass}
        />
        <datalist id="vehicle-interest-options">
          {vehicles.map((vehicle) => (
            <option key={vehicle.id} value={vehicle.label} />
          ))}
        </datalist>
        <p className="mt-1 text-xs text-neutral-400">
          Choose a vehicle from inventory, type a stock number, or leave it blank
          if you are still deciding.
        </p>
      </div>

      <input
        required
        placeholder="Address"
        value={form.address}
        onChange={update("address")}
        className={inputClass}
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <input
            required
            type="text"
            inputMode="numeric"
            placeholder="DOB / Date of birth (mm/dd/yyyy)"
            value={form.dateOfBirth}
            onChange={(e) => {
              markStarted();
              setForm((current) => ({
                ...current,
                dateOfBirth: maskDateOfBirth(e.target.value),
              }));
            }}
            maxLength={10}
            className={inputClass}
          />
          {form.dateOfBirth.length === 10 &&
            !isValidDateOfBirth(form.dateOfBirth) && (
              <p className="mt-1 text-xs text-red-600">
                That date doesn&apos;t look right — please check it.
              </p>
            )}
        </div>
        <input
          placeholder="Driver's license state"
          value={form.driversLicenseState}
          onChange={update("driversLicenseState")}
          className={inputClass}
        />
      </div>

      <label className="flex items-start gap-3 rounded-xl border border-neutral-100 bg-neutral-25 px-4 py-3 text-sm text-neutral-600">
        <input
          type="checkbox"
          checked={form.hasDriversLicense}
          onChange={(e) => {
            markStarted();
            setForm((current) => ({
              ...current,
              hasDriversLicense: e.target.checked,
            }));
          }}
          className="mt-0.5 size-4 accent-primary"
        />
        <span>Yes, I have a driver's license</span>
      </label>

      <textarea
        rows={3}
        placeholder="Anything else we should know? (optional)"
        value={form.message}
        onChange={update("message")}
        className={`${inputClass} resize-none`}
      />

      <label className="flex items-start gap-3 text-xs leading-5 text-neutral-500">
        <input
          type="checkbox"
          checked={smsConsent}
          onChange={(e) => setSmsConsent(e.target.checked)}
          className="mt-0.5 size-4 shrink-0 accent-primary"
        />
        <span>
          Optional: By checking this box, I agree to receive calls and text
          messages from Kelley Autoplex about my inquiry at the phone number
          provided, including via automated technology. Consent is not a
          condition of any purchase or service — you may submit this form
          without checking this box. Msg frequency varies. Msg &amp; data rates
          may apply. Reply STOP to opt out, HELP for help. See our{" "}
          <a href="/privacy-policy" target="_blank" className="font-medium text-primary underline">
            Privacy Policy
          </a>{" "}
          and{" "}
          <a href="/terms-and-conditions" target="_blank" className="font-medium text-primary underline">
            Terms
          </a>
          .
        </span>
      </label>

      {error && <p className="text-sm text-red-500">{error}</p>}

      <button
        type="submit"
        disabled={loading}
        className="rounded-xl bg-gradient-to-b from-[#f9896a] to-primary py-3.5 text-base font-semibold text-white transition-opacity hover:opacity-95 disabled:opacity-60"
      >
        {loading ? "Sending..." : "Get Approved · No Credit Check"}
      </button>
    </form>
  );
}
