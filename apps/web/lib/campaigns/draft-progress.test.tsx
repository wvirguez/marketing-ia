import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import {
  businessStageLabel,
  getDraftPresentationState,
  type DraftPresentationState,
} from "@/lib/campaigns/draft-progress";
import type { BusinessStage, RunProgressPublic, StageExecutionPublic, StageExecutionStatus } from "@/types/orchestration";

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

describe("getDraftPresentationState", () => {
  it("returns NOT_STARTED when the run has not started", () => {
    const progress = makeProgress("CREATED", []);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("NOT_STARTED");
  });

  it("returns GENERATING while running and CONTENT has not completed", () => {
    const progress = makeProgress("RUNNING", [
      makeStage("RESEARCH", "COMPLETED"),
      makeStage("AUDIENCE", "COMPLETED"),
      makeStage("STRATEGY", "COMPLETED"),
      makeStage("PLAN", "COMPLETED"),
      makeStage("CONTENT", "RUNNING"),
    ]);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("GENERATING");
  });

  it("returns DRAFT_GENERATED once CONTENT completes with no bootstrap failure", () => {
    const progress = makeProgress("RUNNING", BOOTSTRAP_COMPLETED);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("DRAFT_GENERATED");
  });

  it("returns FAILED when a bootstrap stage failed", () => {
    const progress = makeProgress("RUNNING", [
      makeStage("RESEARCH", "COMPLETED"),
      makeStage("AUDIENCE", "FAILED"),
      makeStage("STRATEGY", "PENDING"),
      makeStage("PLAN", "PENDING"),
      makeStage("CONTENT", "PENDING"),
    ]);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("FAILED");
  });

  it("prefers FAILED over DRAFT_GENERATED when CONTENT completed but an earlier bootstrap stage also failed", () => {
    const progress = makeProgress("RUNNING", [
      makeStage("RESEARCH", "FAILED"),
      makeStage("AUDIENCE", "COMPLETED"),
      makeStage("STRATEGY", "COMPLETED"),
      makeStage("PLAN", "COMPLETED"),
      makeStage("CONTENT", "COMPLETED"),
    ]);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("FAILED");
  });

  it("ignores a failure outside the bootstrap sequence (e.g. CREATIVE) once CONTENT has completed", () => {
    const progress = makeProgress("RUNNING", [...BOOTSTRAP_COMPLETED, makeStage("CREATIVE", "FAILED")]);
    expect(getDraftPresentationState(progress)).toBe<DraftPresentationState>("DRAFT_GENERATED");
  });
});

describe("businessStageLabel", () => {
  it("maps every bootstrap stage to its Spanish business label", () => {
    expect(businessStageLabel("RESEARCH")).toBe("Investigación");
    expect(businessStageLabel("AUDIENCE")).toBe("Audiencia");
    expect(businessStageLabel("STRATEGY")).toBe("Estrategia");
    expect(businessStageLabel("PLAN")).toBe("Planificación");
    expect(businessStageLabel("CONTENT")).toBe("Contenido");
  });
});

describe("test environment tooling proof (MVP-06C1)", () => {
  it("runs in a jsdom environment", () => {
    expect(document).toBeDefined();
    expect(typeof window).toBe("object");
  });

  it("renders a DOM node that jest-dom's matchers can assert on", () => {
    const node = document.createElement("div");
    node.textContent = "tooling proof";
    document.body.appendChild(node);
    expect(node).toBeInTheDocument();
    document.body.removeChild(node);
  });

  it("renders a React component via @testing-library/react under React 19", () => {
    render(<p>tooling proof</p>);
    expect(screen.getByText("tooling proof")).toBeInTheDocument();
  });

  it("simulates a real user interaction via @testing-library/user-event", async () => {
    function Counter() {
      const [count, setCount] = useState(0);
      return (
        <button type="button" onClick={() => setCount((value) => value + 1)}>
          count: {count}
        </button>
      );
    }
    const user = userEvent.setup();
    render(<Counter />);
    const button = screen.getByRole("button");
    expect(button).toHaveTextContent("count: 0");
    await user.click(button);
    expect(button).toHaveTextContent("count: 1");
  });
});
