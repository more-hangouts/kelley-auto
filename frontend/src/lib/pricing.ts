// Buy-here-pay-here pricing shown on the public storefront.
//
// The dealer runs zero-credit-check, in-house financing and does not want cash
// prices advertised. Per dealer directive (2026-07-06): show a flat down
// payment of "$2,000 for everything for right now." This lives as a single
// constant so it can be raised, or swapped for a per-vehicle down payment,
// without hunting through the components.
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
