import { AuthGate } from "@/components/auth/auth-gate";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate requireStatus="authenticated" redirectTo="/login">
      {children}
    </AuthGate>
  );
}
