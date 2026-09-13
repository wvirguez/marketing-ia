import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsPage } from "@/components/settings/settings-page";
import { useAuth } from "@/lib/auth/auth-context";
import { ApiError } from "@/lib/api/client";
import type { AuthContextValue } from "@/lib/auth/auth-context";
import type { WorkspaceSettingsResponse } from "@/types/settings";
import type { UserPublic } from "@/types/auth";

vi.mock("@/lib/auth/auth-context", () => ({
  useAuth: vi.fn(),
}));
vi.mock("@/lib/api/settings", () => ({
  getWorkspaceSettings: vi.fn(),
  updateWorkspaceSettings: vi.fn(),
}));
vi.mock("@/lib/api/auth", () => ({
  updateCurrentUser: vi.fn(),
}));

import { getWorkspaceSettings, updateWorkspaceSettings } from "@/lib/api/settings";
import { updateCurrentUser } from "@/lib/api/auth";

const mockUseAuth = vi.mocked(useAuth);
const mockGetWorkspaceSettings = vi.mocked(getWorkspaceSettings);
const mockUpdateWorkspaceSettings = vi.mocked(updateWorkspaceSettings);
const mockUpdateCurrentUser = vi.mocked(updateCurrentUser);

beforeEach(() => {
  vi.clearAllMocks();
});

function makeUser(overrides: Partial<UserPublic> = {}): UserPublic {
  return {
    id: "USR-1",
    email: "real.user@impulso.test",
    display_name: "Real User",
    status: "ACTIVE",
    preferences: { locale: "es", timezone: "America/Toronto" },
    ...overrides,
  };
}

type AuthenticatedAuthContextValue = Extract<AuthContextValue, { status: "authenticated" }>;

function makeSession(overrides: Partial<AuthenticatedAuthContextValue["session"]> = {}, role = "OWNER"): AuthenticatedAuthContextValue["session"] {
  return {
    user: makeUser(),
    workspace: { id: "WS-1", name: "Real Workspace", slug: "real-workspace" },
    membership: { role },
    ...overrides,
  };
}

function makeAuth(overrides: Partial<AuthenticatedAuthContextValue> = {}, role = "OWNER"): AuthContextValue {
  return {
    status: "authenticated",
    session: makeSession({}, role),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

function settings(overrides: Partial<WorkspaceSettingsResponse> = {}): WorkspaceSettingsResponse {
  return {
    workspace: { name: "Real Workspace" },
    ai_preferences: { tone: null, depth: null, creativity: null },
    notifications: {
      campaign_ready: true,
      content_review: true,
      metrics_available: true,
      analysis_complete: false,
      weekly_summary: true,
    },
    ...overrides,
  };
}

async function openTab(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(screen.getByRole("tab", { name: label }));
}

describe("SettingsPage — data loading", () => {
  it("performs a real workspace settings GET on mount", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    await waitFor(() => expect(mockGetWorkspaceSettings).toHaveBeenCalledWith("WS-1"));
  });

  it("issues only one GET on initial mount", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    await waitFor(() => expect(mockGetWorkspaceSettings).toHaveBeenCalledTimes(1));
  });

  it("does not refetch when switching settings tabs", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await waitFor(() => expect(mockGetWorkspaceSettings).toHaveBeenCalledTimes(1));

    await openTab(user, "Espacio de trabajo");
    await openTab(user, "Preferencias de IA");
    await openTab(user, "Notificaciones");
    await openTab(user, "Integraciones");
    await openTab(user, "Plan y facturación");
    await openTab(user, "Perfil");

    expect(mockGetWorkspaceSettings).toHaveBeenCalledTimes(1);
  });

  it("renders the server workspace name", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings({ workspace: { name: "Acme Studio" } }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    expect(await screen.findByDisplayValue("Acme Studio")).toBeInTheDocument();
  });

  it("renders the server AI preferences", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(
      settings({ ai_preferences: { tone: "cercano", depth: null, creativity: null } }),
    );
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    expect(await screen.findByRole("radio", { name: "Cercano" })).toBeChecked();
  });

  it("renders the server notification booleans", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    expect(await screen.findByRole("switch", { name: /Análisis completado/i })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("switch", { name: /Campaña lista/i })).toHaveAttribute("aria-checked", "true");
  });

  it("shows a retry affordance when the initial GET fails, and retry genuinely reloads", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockRejectedValueOnce(new ApiError(500, "INTERNAL", "boom"));
    mockGetWorkspaceSettings.mockResolvedValueOnce(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    const retry = await screen.findByRole("button", { name: "Reintentar" });

    await user.click(retry);

    await waitFor(() => expect(mockGetWorkspaceSettings).toHaveBeenCalledTimes(2));
    expect(await screen.findByDisplayValue("Real Workspace")).toBeInTheDocument();
  });
});

describe("SettingsPage — stale demo data removed", () => {
  it("never renders Emilia Martínez / emilia@ejemplo.com / DEMO-WORKSPACE-001 / Studio Demo anywhere", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    const { container } = render(<SettingsPage />);
    for (const tab of ["Perfil", "Espacio de trabajo", "Preferencias de IA", "Notificaciones", "Integraciones", "Plan y facturación"]) {
      await openTab(user, tab);
    }
    await waitFor(() => expect(mockGetWorkspaceSettings).toHaveBeenCalled());
    expect(container.textContent).not.toContain("Emilia Martínez");
    expect(container.textContent).not.toContain("emilia@ejemplo.com");
    expect(container.textContent).not.toContain("DEMO-WORKSPACE-001");
    expect(container.textContent).not.toContain("Studio Demo");
  });
});

describe("SettingsPage — Profile section", () => {
  it("renders real session user data (display name, email)", async () => {
    mockUseAuth.mockReturnValue(
      makeAuth({ session: makeSession({ user: makeUser({ display_name: "Jordan Ríos", email: "jordan@impulso.test" }) }) }),
    );
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    expect(await screen.findByDisplayValue("Jordan Ríos")).toBeInTheDocument();
    expect(screen.getByDisplayValue("jordan@impulso.test")).toBeInTheDocument();
  });

  it("renders email as read-only", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    const email = await screen.findByLabelText("Correo");
    expect(email).toBeDisabled();
  });

  it("renders no firstName/lastName/Cargo/avatar editable controls", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    await screen.findByLabelText("Correo");
    expect(screen.queryByLabelText(/apellido/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/cargo/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/avatar/i)).not.toBeInTheDocument();
  });

  it("submitting a changed display name sends exactly {display_name}, no preferences key", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateCurrentUser.mockResolvedValue(makeUser({ display_name: "Updated Name" }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    const nameInput = await screen.findByLabelText("Nombre");

    await user.clear(nameInput);
    await user.type(nameInput, "Updated Name");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    await waitFor(() => expect(mockUpdateCurrentUser).toHaveBeenCalledWith({ display_name: "Updated Name" }));
  });

  it("Profile PATCH success uses the returned UserPublic as displayed truth", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateCurrentUser.mockResolvedValue(makeUser({ display_name: "Server Confirmed Name" }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    const nameInput = await screen.findByLabelText("Nombre");
    await user.clear(nameInput);
    await user.type(nameInput, "Whatever I typed");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    expect(await screen.findByDisplayValue("Server Confirmed Name")).toBeInTheDocument();
  });

  it("Profile PATCH success invokes auth.refresh()", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    mockUseAuth.mockReturnValue(makeAuth({ refresh }));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateCurrentUser.mockResolvedValue(makeUser({ display_name: "Updated" }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    const nameInput = await screen.findByLabelText("Nombre");
    await user.clear(nameInput);
    await user.type(nameInput, "Updated");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  });

  it("a PATCH success is never reclassified as a failure merely because auth.refresh() subsequently rejects", async () => {
    const refresh = vi.fn().mockRejectedValue(new Error("refresh network blip"));
    mockUseAuth.mockReturnValue(makeAuth({ refresh }));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateCurrentUser.mockResolvedValue(makeUser({ display_name: "Updated" }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    const nameInput = await screen.findByLabelText("Nombre");
    await user.clear(nameInput);
    await user.type(nameInput, "Updated");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("Profile PATCH failure preserves the user's draft and shows an inline error", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateCurrentUser.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<SettingsPage />);
    const nameInput = await screen.findByLabelText("Nombre");
    await user.clear(nameInput);
    await user.type(nameInput, "Draft Not Yet Saved");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Draft Not Yet Saved")).toBeInTheDocument();
  });
});

describe("SettingsPage — Workspace section", () => {
  it("an OWNER can edit and save exactly {workspace:{name}}", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "OWNER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateWorkspaceSettings.mockResolvedValue(settings({ workspace: { name: "New Name" } }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    const nameInput = await screen.findByLabelText("Nombre del espacio");
    await user.clear(nameInput);
    await user.type(nameInput, "New Name");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    await waitFor(() =>
      expect(mockUpdateWorkspaceSettings).toHaveBeenCalledWith("WS-1", { workspace: { name: "New Name" } }),
    );
  });

  it("a MEMBER sees the field disabled and cannot submit a mutation", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "MEMBER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    const nameInput = await screen.findByLabelText("Nombre del espacio");

    expect(nameInput).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Guardar cambios" })).not.toBeInTheDocument();
    expect(mockUpdateWorkspaceSettings).not.toHaveBeenCalled();
  });

  it("renders no unsupported workspace fields", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    await screen.findByLabelText("Nombre del espacio");
    expect(screen.queryByLabelText(/tipo de negocio/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/sitio web/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/país/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/moneda/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/workspace id/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /eliminar/i })).not.toBeInTheDocument();
  });

  it("Workspace PATCH failure preserves the last confirmed value and the typed draft", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "OWNER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings({ workspace: { name: "Confirmed Name" } }));
    mockUpdateWorkspaceSettings.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    const nameInput = await screen.findByLabelText("Nombre del espacio");
    await user.clear(nameInput);
    await user.type(nameInput, "Attempted Name");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Attempted Name")).toBeInTheDocument();
  });
});

describe("SettingsPage — AI Preferences section", () => {
  it("an OWNER can edit AI preferences", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "OWNER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    const tone = await screen.findByRole("radio", { name: "Cercano" });
    expect(tone).not.toBeDisabled();
  });

  it("a MEMBER cannot submit an AI preferences mutation", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "MEMBER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    const tone = await screen.findByRole("radio", { name: "Cercano" });
    expect(tone).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Guardar preferencias" })).not.toBeInTheDocument();
    expect(mockUpdateWorkspaceSettings).not.toHaveBeenCalled();
  });

  it("uses the frozen lowercase machine values as radio option values", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    const tone = await screen.findByRole("radio", { name: "Profesional" });
    expect(tone).toHaveAttribute("value", "profesional");
  });

  it("server null renders no radio pre-selected", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings({ ai_preferences: { tone: null, depth: null, creativity: null } }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    await screen.findByRole("radio", { name: "Profesional" });
    for (const radio of screen.getAllByRole("radio")) {
      expect(radio).not.toBeChecked();
    }
  });

  it("changing only tone sends {ai_preferences:{tone}} only", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateWorkspaceSettings.mockResolvedValue(settings({ ai_preferences: { tone: "directo", depth: null, creativity: null } }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    await user.click(await screen.findByRole("radio", { name: "Directo" }));
    await user.click(screen.getByRole("button", { name: "Guardar preferencias" }));

    await waitFor(() =>
      expect(mockUpdateWorkspaceSettings).toHaveBeenCalledWith("WS-1", { ai_preferences: { tone: "directo" } }),
    );
  });

  it("a no-op AI save (nothing changed) leaves the save button disabled and sends no PATCH", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    await screen.findByRole("radio", { name: "Profesional" });
    expect(screen.getByRole("button", { name: "Guardar preferencias" })).toBeDisabled();
    expect(mockUpdateWorkspaceSettings).not.toHaveBeenCalled();
  });

  it("renders no responseLanguage or requireApproval control", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    await screen.findByRole("radio", { name: "Profesional" });
    expect(screen.queryByLabelText(/idioma de respuesta/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/solicitar revisión antes de avanzar/i)).not.toBeInTheDocument();
  });

  it("AI PATCH failure preserves confirmed values and the in-progress draft", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings({ ai_preferences: { tone: "profesional", depth: null, creativity: null } }));
    mockUpdateWorkspaceSettings.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Preferencias de IA");
    await user.click(await screen.findByRole("radio", { name: "Educativo" }));
    await user.click(screen.getByRole("button", { name: "Guardar preferencias" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Educativo" })).toBeChecked();
  });
});

describe("SettingsPage — Notifications section", () => {
  it("renders no 'Guardar cambios' button for Notifications", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    await screen.findByRole("switch", { name: /Campaña lista/i });
    expect(screen.queryByRole("button", { name: /guardar/i })).not.toBeInTheDocument();
  });

  it("toggling one switch sends exactly {notifications:{<key>: value}}", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateWorkspaceSettings.mockResolvedValue(settings({ notifications: { ...settings().notifications, weekly_summary: false } }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    await user.click(await screen.findByRole("switch", { name: /Resumen semanal/i }));

    await waitFor(() =>
      expect(mockUpdateWorkspaceSettings).toHaveBeenCalledWith("WS-1", { notifications: { weekly_summary: false } }),
    );
  });

  it("a successful toggle renders the server's returned merged notifications state", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateWorkspaceSettings.mockResolvedValue(
      settings({ notifications: { campaign_ready: true, content_review: true, metrics_available: true, analysis_complete: true, weekly_summary: false } }),
    );
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    await user.click(await screen.findByRole("switch", { name: /Resumen semanal/i }));

    await waitFor(() => expect(screen.getByRole("switch", { name: /Análisis completado/i })).toHaveAttribute("aria-checked", "true"));
  });

  it("a failed toggle reverts to the last confirmed server value and shows an inline alert", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    mockUpdateWorkspaceSettings.mockRejectedValue(new ApiError(500, "INTERNAL", "boom"));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    const weeklySwitch = await screen.findByRole("switch", { name: /Resumen semanal/i });
    expect(weeklySwitch).toHaveAttribute("aria-checked", "true");

    await user.click(weeklySwitch);

    await screen.findByRole("alert");
    expect(weeklySwitch).toHaveAttribute("aria-checked", "true");
  });

  it("serializes notification mutations: while one PATCH is pending, ALL switches are disabled and a second click starts no new request", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    let resolvePatch: (value: WorkspaceSettingsResponse) => void = () => {};
    mockUpdateWorkspaceSettings.mockImplementation(() => new Promise((resolve) => { resolvePatch = resolve; }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    const weeklySwitch = await screen.findByRole("switch", { name: /Resumen semanal/i });
    const campaignSwitch = screen.getByRole("switch", { name: /Campaña lista/i });
    const contentSwitch = screen.getByRole("switch", { name: /Contenido listo para revisión/i });
    const metricsSwitch = screen.getByRole("switch", { name: /Métricas disponibles/i });
    const analysisSwitch = screen.getByRole("switch", { name: /Análisis completado/i });

    await user.click(weeklySwitch);

    // While pending: the clicked switch is busy, and every switch in the
    // section — including the four never clicked — is disabled. This is
    // the actual repair: a per-key model would leave the other four
    // enabled here.
    expect(weeklySwitch).toHaveAttribute("aria-busy", "true");
    expect(weeklySwitch).toBeDisabled();
    expect(campaignSwitch).toBeDisabled();
    expect(contentSwitch).toBeDisabled();
    expect(metricsSwitch).toBeDisabled();
    expect(analysisSwitch).toBeDisabled();

    // A second click on a DIFFERENT toggle while the first is still
    // unresolved must start no second request at all — the disabled
    // attribute already prevents userEvent from dispatching the click,
    // and the component's own submittingKey guard is defense-in-depth.
    await user.click(campaignSwitch);
    expect(mockUpdateWorkspaceSettings).toHaveBeenCalledTimes(1);

    resolvePatch(settings({ notifications: { ...settings().notifications, weekly_summary: false } }));

    await waitFor(() => expect(weeklySwitch).not.toBeDisabled());
    expect(campaignSwitch).not.toBeDisabled();
    expect(weeklySwitch).toHaveAttribute("aria-checked", "false");

    // Only after the first request settles may a second, genuinely new
    // request be issued.
    mockUpdateWorkspaceSettings.mockResolvedValue(settings({ notifications: { ...settings().notifications, campaign_ready: false } }));
    await user.click(campaignSwitch);
    expect(mockUpdateWorkspaceSettings).toHaveBeenCalledTimes(2);
  });

  it("a failed notification PATCH unlocks all switches, leaves confirmed state unchanged, and allows a fresh retry", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    let rejectPatch: (error: unknown) => void = () => {};
    mockUpdateWorkspaceSettings.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectPatch = reject; }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Notificaciones");
    const weeklySwitch = await screen.findByRole("switch", { name: /Resumen semanal/i });
    const campaignSwitch = screen.getByRole("switch", { name: /Campaña lista/i });
    expect(weeklySwitch).toHaveAttribute("aria-checked", "true");

    await user.click(weeklySwitch);
    expect(weeklySwitch).toBeDisabled();
    expect(campaignSwitch).toBeDisabled();

    rejectPatch(new ApiError(500, "INTERNAL", "boom"));

    await screen.findByRole("alert");
    expect(mockUpdateWorkspaceSettings).toHaveBeenCalledTimes(1);
    // Confirmed state (server truth) is unchanged by the failure — no
    // optimistic value was ever retained.
    expect(weeklySwitch).toHaveAttribute("aria-checked", "true");
    expect(weeklySwitch).not.toBeDisabled();
    expect(campaignSwitch).not.toBeDisabled();

    // A fresh click after the failure is a genuinely new, allowed request.
    mockUpdateWorkspaceSettings.mockResolvedValueOnce(settings({ notifications: { ...settings().notifications, weekly_summary: false } }));
    await user.click(weeklySwitch);
    await waitFor(() => expect(mockUpdateWorkspaceSettings).toHaveBeenCalledTimes(2));
  });
});

describe("SettingsPage — Integrations section", () => {
  it("renders no 'Conectar' button", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Integraciones");
    expect(screen.queryByRole("button", { name: /conectar/i })).not.toBeInTheDocument();
  });

  it("renders no fake connected/disconnected status", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Integraciones");
    expect(screen.queryByText(/no conectado/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/conectado/i)).not.toBeInTheDocument();
  });

  it("explicitly states future/próximamente language", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Integraciones");
    expect(screen.getAllByText(/próximamente/i).length).toBeGreaterThan(0);
  });
});

describe("SettingsPage — Billing section", () => {
  it("renders no Studio Demo / current-plan state", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Plan y facturación");
    expect(screen.queryByText("Studio Demo")).not.toBeInTheDocument();
    expect(screen.queryByText(/demostración/i)).not.toBeInTheDocument();
  });

  it("still explicitly states future/preview/no real billing", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Plan y facturación");
    expect(screen.getByText(/no hay facturación real conectada/i)).toBeInTheDocument();
  });
});

describe("SettingsPage — cross-cutting", () => {
  it("no mutation reports success before its Promise resolves", async () => {
    mockUseAuth.mockReturnValue(makeAuth({}, "OWNER"));
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    let resolvePatch: (value: WorkspaceSettingsResponse) => void = () => {};
    mockUpdateWorkspaceSettings.mockImplementation(() => new Promise((resolve) => { resolvePatch = resolve; }));
    const user = userEvent.setup();
    render(<SettingsPage />);
    await openTab(user, "Espacio de trabajo");
    const nameInput = await screen.findByLabelText("Nombre del espacio");
    await user.clear(nameInput);
    await user.type(nameInput, "Pending Name");
    await user.click(screen.getByRole("button", { name: "Guardar cambios" }));

    expect(screen.queryByText("Cambios guardados.")).not.toBeInTheDocument();
    resolvePatch(settings({ workspace: { name: "Pending Name" } }));
    expect(await screen.findByText("Cambios guardados.")).toBeInTheDocument();
  });

  it("preserves the original 6-tab order after integration", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    const tabLabels = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabLabels).toEqual(["Perfil", "Espacio de trabajo", "Preferencias de IA", "Integraciones", "Notificaciones", "Plan y facturación"]);
  });

  it("keyboard ArrowDown moves selection to the next tab", async () => {
    mockUseAuth.mockReturnValue(makeAuth());
    mockGetWorkspaceSettings.mockResolvedValue(settings());
    render(<SettingsPage />);
    const profileTab = screen.getByRole("tab", { name: "Perfil" });
    profileTab.focus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("tab", { name: "Espacio de trabajo" })).toHaveAttribute("aria-selected", "true");
  });
});
