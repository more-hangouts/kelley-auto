// Pricing shown on the public storefront. Which line a car gets depends on
// how it is sold (`saleType`, migration 103):
//
//   bhph — the dealer carries the note. Per dealer directive (2026-07-06):
//     do not advertise a cash price; show a flat "$2,000 for everything for
//     right now" down payment. The 2026-07-06 rule was written when the whole
//     lot was buy-here-pay-here, and still governs every bhph car.
//
//   cash — sold outright, no financing offered. A down payment line here is
//     simply false: there is no note to put money down on. The asking price
//     is the number the shopper came for, so cash cars show it.
//
// The split is deliberate and narrow: flagging a car 'cash' is the only thing
// that reveals its price, so the no-cash-prices rule still holds for the rest
// of the lot.
export const DOWN_PAYMENT_BASE = 2000;

/** e.g. "$2,000" */
export function formatDownPayment(amount: number = DOWN_PAYMENT_BASE): string {
  return `$${amount.toLocaleString()}`;
}

/** e.g. "Down Payment: $2,000" — the storefront's headline price line.
 *  Stated definitively (not "as low as …") per dealer directive so the
 *  buy-here-pay-here entry price reads as a firm number. */
export function downPaymentHeadline(amount: number = DOWN_PAYMENT_BASE): string {
  return `Down Payment: ${formatDownPayment(amount)}`;
}

/** e.g. "$5,900" for a cash car, or a fallback when no price is set.
 *  Never falls back to the down-payment line: a cash car with no price on
 *  file should ask the shopper to call, not quote a financing figure that
 *  doesn't apply to it. */
export function formatCashPrice(price: number | null | undefined): string {
  if (typeof price !== "number" || !Number.isFinite(price) || price <= 0) {
    return "Call for price";
  }
  return `$${Math.round(price).toLocaleString()}`;
}
