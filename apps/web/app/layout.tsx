import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Workforce — Agent control plane',
  description: 'Run and govern your Operations and Customer Support agents.',
};

export default function RootLayout({ children }: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body>{children}</body></html>;
}
