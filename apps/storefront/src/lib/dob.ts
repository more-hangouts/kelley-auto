// Date-of-birth entry helpers for the BHPH intake forms.
//
// The DOB inputs use `inputMode="numeric"` so applicants get a phone keypad —
// which has no "/" key. Left unmasked, most people type "08101995", and that
// bare-digit string is what lands in the encrypted column. It is not a date any
// parser can read without guessing, so the CRM used to render nonsense for it.
//
// Masking as the applicant types keeps the stored value canonical at the source
// instead of asking every reader to re-guess the format later.

/** Progressively format digits as MM/DD/YYYY while the applicant types.
 *  Non-digits are dropped, so pasting "08-10-1995" also lands correctly, and
 *  deleting through a slash behaves (the slash is re-derived, never sticky). */
export function maskDateOfBirth(input: string): string {
  const digits = input.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
  return `${digits.slice(0, 2)}/${digits.slice(2, 4)}/${digits.slice(4)}`;
}

/** True when `value` is a complete MM/DD/YYYY date that could be a real
 *  birthday. Rejects impossible calendar dates ("02/31/1990"), the future, and
 *  ages outside 0–120 — a typo'd year is the most common bad entry and the one
 *  most likely to be believed downstream. */
export function isValidDateOfBirth(value: string): boolean {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
  if (!match) return false;
  const [, mm, dd, yyyy] = match;
  const month = Number(mm);
  const day = Number(dd);
  const year = Number(yyyy);
  const parsed = new Date(year, month - 1, day);
  // Round-trip guard: JS Date rolls 02/31 forward to 03/03 rather than failing.
  if (
    parsed.getFullYear() !== year ||
    parsed.getMonth() !== month - 1 ||
    parsed.getDate() !== day
  ) {
    return false;
  }
  const now = new Date();
  if (parsed > now) return false;
  const age = (now.getTime() - parsed.getTime()) / (365.25 * 24 * 60 * 60 * 1000);
  return age <= 120;
}
