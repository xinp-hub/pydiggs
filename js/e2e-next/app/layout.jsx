export const metadata = { title: "@pydiggs/client in Next.js" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
