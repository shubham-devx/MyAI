import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'V-Sutra',
  description: 'V-Sutra independent AI platform with local Ollama integration',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className="dark">
      <body className="app-body">
        <div className="app-frame">
          {children}
        </div>
      </body>
    </html>
  )
}
