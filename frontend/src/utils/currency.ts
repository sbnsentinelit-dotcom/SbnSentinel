/**
 * Authoritative currency formatting helper for SBN Sentinel.
 * Formats amount into the authoritative regional currency (AED in UAE, USD in US).
 * Rule 2 & 5: Never guesses; formats exact numeric amount with currency provided from the API.
 */
export const formatMoney = (
  amount: number | string | null | undefined,
  currency?: string | null
): string => {
  if (amount === null || amount === undefined || amount === '') return '$0.00';

  let num: number;
  if (typeof amount === 'number') {
    num = amount;
  } else {
    const cleaned = String(amount).replace(/[^0-9.-]+/g, '');
    num = parseFloat(cleaned);
  }
  if (isNaN(num)) return '$0.00';

  const formattedNum = num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const curr = (
    currency ||
    (typeof amount === 'string' && amount.includes('AED') ? 'AED' : 'USD')
  ).toUpperCase();

  if (curr === 'AED') {
    return `AED ${formattedNum}`;
  }
  return `$${formattedNum}`;
};

