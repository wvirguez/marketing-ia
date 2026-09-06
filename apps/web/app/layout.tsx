import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Impulso — Marketing AI",
  description: "Plataforma inteligente para planificar, crear y optimizar campañas de marketing.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return <html lang="es"><body>{children}</body></html>;
}
