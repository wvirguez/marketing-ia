import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GenerateDraftAction } from "@/components/campaigns/detail/generate-draft-action";
import type { CampaignRunPublic } from "@/types/campaign";
import type { BusinessStage, RunProgressPublic, StageExecutionPublic, StageExecutionStatus } from "@/types/orchestration";

vi.mock("@/lib/api/orchestration", () => ({
  initializeCampaignRun: vi.fn(),
  startCampaignRun: vi.fn(),
  getCampaignRunProgress: vi.fn(),
}));

import { getCampaignRunProgress, initializeCampaignRun, startCampaignRun } from "@/lib/api/orchestration";

const mockGetProgress = vi.mocked(getCampaignRunProgress);
const mockInitialize = vi.mocked(initializeCampaignRun);
const mockStart = vi.mocked(startCampaignRun);

function makeRun(status: string): CampaignRunPublic {
  return { id: "run-1", campaign_id: "campaign-1", run_number: 1, status, created_at: "2026-01-01T00:00:00Z" };
}

function makeStage(stage: BusinessStage, status: StageExecutionStatus): StageExecutionPublic {
  return {
    id: `stage-${stage}`,
    stage,
    ordinal: 0,
    status,
    started_at: null,
    completed_at: null,
    blocked_reason: null,
    failure_reason: null,
  };
}

function makeProgress(run_status: string, stages: StageExecutionPublic[]): RunProgressPublic {
  return {
    campaign_id: "campaign-1",
    run_id: "run-1",
    run_status,
    current_stage: null,
    stages,
    waiting_for_input: false,
    open_decision_count: 0,
  };
}

const BOOTSTRAP_COMPLETED: StageExecutionPublic[] = [
  makeStage("RESEARCH", "COMPLETED"),
  makeStage("AUDIENCE", "COMPLETED"),
  makeStage("STRATEGY", "COMPLETED"),
  makeStage("PLAN", "COMPLETED"),
  makeStage("CONTENT", "COMPLETED"),
];

const SUCCESS_COPY = "Borrador inicial generado.";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("GenerateDraftAction", () => {
  it("renders the generation CTA for a CREATED run", async () => {
    mockGetProgress.mockResolvedValue(makeProgress("CREATED", []));
    render(<GenerateDraftAction campaignId="campaign-1" runs={[makeRun("CREATED")]} />);

    expect(await screen.findByRole("button", { name: /generar borrador/i })).toBeInTheDocument();
  });

  it("runs initialize -> start -> refresh progress in order and hides the CTA on success", async () => {
    mockGetProgress
      .mockResolvedValueOnce(makeProgress("CREATED", []))
      .mockResolvedValueOnce(makeProgress("RUNNING", BOOTSTRAP_COMPLETED));
    mockInitialize.mockResolvedValue({ items: [] });
    mockStart.mockResolvedValue(makeRun("RUNNING"));

    const user = userEvent.setup();
    render(<GenerateDraftAction campaignId="campaign-1" runs={[makeRun("CREATED")]} />);

    const button = await screen.findByRole("button", { name: /generar borrador/i });
    await user.click(button);

    expect(await screen.findByText(SUCCESS_COPY)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generar borrador/i })).not.toBeInTheDocument();

    expect(mockInitialize).toHaveBeenCalledWith("campaign-1", "run-1");
    expect(mockStart).toHaveBeenCalledWith("campaign-1", "run-1");
    expect(mockGetProgress).toHaveBeenCalledTimes(2);

    const initOrder = mockInitialize.mock.invocationCallOrder[0];
    const startOrder = mockStart.mock.invocationCallOrder[0];
    const secondProgressOrder = mockGetProgress.mock.invocationCallOrder[1];
    expect(initOrder).toBeLessThan(startOrder);
    expect(startOrder).toBeLessThan(secondProgressOrder);
  });

  it("disables the button while submitting and does not start a second sequence on a repeated click", async () => {
    mockGetProgress
      .mockResolvedValueOnce(makeProgress("CREATED", []))
      .mockResolvedValueOnce(makeProgress("RUNNING", BOOTSTRAP_COMPLETED));
    let resolveInitialize!: (value: { items: never[] }) => void;
    mockInitialize.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveInitialize = resolve;
        }),
    );
    mockStart.mockResolvedValue(makeRun("RUNNING"));

    const user = userEvent.setup();
    render(<GenerateDraftAction campaignId="campaign-1" runs={[makeRun("CREATED")]} />);

    const button = await screen.findByRole("button", { name: /generar borrador/i });
    await user.click(button);

    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");

    await user.click(button);
    expect(mockInitialize).toHaveBeenCalledTimes(1);

    resolveInitialize({ items: [] });
    expect(await screen.findByText(SUCCESS_COPY)).toBeInTheDocument();
  });

  it("renders the successful draft-generated presentation once CONTENT has completed, without a raw RUNNING contradiction", async () => {
    mockGetProgress.mockResolvedValue(makeProgress("RUNNING", BOOTSTRAP_COMPLETED));
    render(<GenerateDraftAction campaignId="campaign-1" runs={[makeRun("RUNNING")]} />);

    expect(await screen.findByText(SUCCESS_COPY)).toBeInTheDocument();
    expect(screen.queryByText(/en ejecución/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generar borrador/i })).not.toBeInTheDocument();
  });

  it("renders incomplete-draft guidance naming the failed business stage when a bootstrap stage fails", async () => {
    mockGetProgress.mockResolvedValue(
      makeProgress("RUNNING", [
        makeStage("RESEARCH", "COMPLETED"),
        makeStage("AUDIENCE", "FAILED"),
        makeStage("STRATEGY", "PENDING"),
        makeStage("PLAN", "PENDING"),
        makeStage("CONTENT", "PENDING"),
      ]),
    );
    render(<GenerateDraftAction campaignId="campaign-1" runs={[makeRun("RUNNING")]} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Audiencia");
    expect(alert).toHaveTextContent("pueden seguir disponibles");
    expect(alert.textContent).not.toMatch(/AGENT/i);
    expect(screen.queryByText(/en ejecución/i)).not.toBeInTheDocument();
  });
});
