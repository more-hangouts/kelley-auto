"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { submitLead } from "@/lib/publicApi";
import { getTrackingContext, track } from "@/lib/analytics";

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
    setLoading(true);
    setError(null);

    const selectedVehicle = vehicleByLabel.get(form.interestedVehicle.trim());
    const listingCode =
      selectedVehicle?.listingCode ||
      listingCodeFromInterest(form.interestedVehicle);
    // Sales-context only (incl. the customer's own free-text Notes). The PII
    // — address, DOB, license — is sent as discrete structured fields below
    // and encrypted at rest server-side; it must never enter `message`, which
    // lands in the deal notes.
    const message = [
      "Standard approval form.",
      "Customer wants to get approved with no credit check.",
      form.interestedVehicle.trim()
        ? `Interested vehicle: ${form.interestedVehicle.trim()}`
        : "Interested vehicle: Not sure yet",
      form.message.trim() ? `Notes: ${form.message.trim()}` : null,
    ]
      .filter(Boolean)
      .join("\n");

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
      // Attribution: ties this lead to the visitor's browsing journey. The
      // server records the authoritative `lead_submitted` event using
      // event_id (shared later with the Meta Pixel/CAPI for dedup).
      tracking: getTrackingContext(),
    });

    if (result.ok) {
      setSent(true);
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
        <input
          required
          type="text"
          inputMode="numeric"
          placeholder="DOB / Date of birth (mm/dd/yyyy)"
          value={form.dateOfBirth}
          onChange={update("dateOfBirth")}
          className={inputClass}
        />
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
