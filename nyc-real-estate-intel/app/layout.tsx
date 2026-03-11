import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'NYC Real Estate Intelligence',
  description: 'Comprehensive property intelligence for NYC real estate professionals',
  keywords: ['NYC real estate', 'property data', 'construction permits', 'building intelligence'],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-background antialiased">
        {children}
      </body>
    </html>
  )
}
