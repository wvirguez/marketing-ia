import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Dashboard | Impulso",
  description: "Tu espacio para crear campañas y contenidos con inteligencia artificial. Vista de demostración.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="es"><body>{children}</body></html>;
}
