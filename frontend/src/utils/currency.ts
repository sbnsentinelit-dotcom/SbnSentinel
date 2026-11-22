/**
 * Authoritative currency formatting helper for SBN Sentinel.
 * Formats amount into the authoritative regional currency (AED in UAE, USD in US).
 * Rule 2 & 5: Never guesses; formats exact numeric amount with currency provided from the API.
 * Preserves genuine 0 as $0.00 / AED 0.00, displays missing values as 'Unavailable',
 * and formats numeric value without guessing when currency is omitted.
 */
export const formatMoney = (
  amount: number | string | null | undefined,
  currency?: string | null
): string => {
  if (amount === null || amount === undefined || amount === '') {
    return 'Unavailable';
  }

  let num: number;
  if (typeof amount === 'number') {
    num = amount;
  } else {
    const cleaned = String(amount).replace(/[^0-9.-]+/g, '');
    if (cleaned === '' || cleaned === '-') return 'Unavailable';
    num = parseFloat(cleaned);
  }
  if (isNaN(num)) return 'Unavailable';

  const formattedNum = num.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  if (!currency) {
    // When currency parameter is absent, never guess or infer currency
    return formattedNum;
  }

  const curr = currency.trim().toUpperCase();
  if (curr === 'USD') {
    return `$${formattedNum}`;
  }
  if (curr === 'AED') {
    return `AED ${formattedNum}`;
  }
  return `${currency} ${formattedNum}`;
};
