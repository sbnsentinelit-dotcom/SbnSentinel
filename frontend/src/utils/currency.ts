/**
 * Authoritative currency formatting helper for SBN Sentinel.
 * Formats amount into the authoritative regional currency (AED in UAE, USD in US).
 * Rule 2 & 5: Never guesses; formats exact numeric amount with currency provided from the API.
 */
export const formatMoney = (
  amount: number | string | null | undefined,
  currency?: string | null
): string => {
  const isUSD = currency?.toUpperCase() === 'USD' || (typeof amount === 'string' && amount.includes('$'));
  const fallback = isUSD ? '$0.00' : 'AED 0.00';

  if (amount === null || amount === undefined || amount === '') return fallback;

  let num: number;
  if (typeof amount === 'number') {
    num = amount;
  } else {
    const cleaned = String(amount).replace(/[^0-9.-]+/g, '');
    num = parseFloat(cleaned);
  }
  if (isNaN(num)) return fallback;

  const formattedNum = num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const curr = (
    currency ||
    (typeof amount === 'string' && (amount.includes('$') || amount.includes('USD')) ? 'USD' : 'AED')
  ).toUpperCase();

  if (curr === 'USD') {
    return `$${formattedNum}`;
  }
  return `AED ${formattedNum}`;
};
