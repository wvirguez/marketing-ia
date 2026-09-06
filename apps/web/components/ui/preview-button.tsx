import type { ButtonHTMLAttributes } from "react";

export function PreviewButton({ children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button type="button" {...props} aria-disabled="true" title="Disponible próximamente · Vista de demostración">{children}</button>;
}
