import type { Metadata } from "next";
import "./globals.css";

import StoreProvider from '@/providers/StoreProvider';
import { ErrorScreen } from '@/components/CommandCenter/ErrorScreen';

export const metadata: Metadata = {
  title: "SBN Sentinel",
  description: "Platform Foundation",
};

// SDS-D9: Localization/RTL readiness contract
const uiLocale = {
  locale: process.env.NEXT_PUBLIC_LOCALE || 'en',
  dir: (process.env.NEXT_PUBLIC_DIR || 'ltr') as 'ltr' | 'rtl'
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang={uiLocale.locale} dir={uiLocale.dir} className="dark">
      <body className="font-sans antialiased bg-[var(--color-canvas)] text-[var(--color-text-primary)]">
        <StoreProvider>
          <ErrorScreen>
            <div className="min-h-screen relative w-full overflow-hidden animate-fade-in">
              {children}
            </div>
          </ErrorScreen>
        </StoreProvider>
      </body>
    </html>
  );
}
