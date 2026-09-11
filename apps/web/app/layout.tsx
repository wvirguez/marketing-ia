import type { Metadata } from "next";
import { AuthProvider } from "@/lib/auth/auth-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "Impulso — Marketing AI",
  description: "Plataforma inteligente para planificar, crear y optimizar campañas de marketing.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="es">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
