/** Shared chat / plan types used across App and panels. */

export type PlanTaskStatus =
  | "pending"
  | "running"
  | "done"
  | "skipped"
  | "error"
  | "cancelled";

export type PlanTask = {
  id: string;
  title: string;
  detail?: string;
  status: PlanTaskStatus;
};

export type ShapeContract = {
  reuse?: string;
  create_only_if?: string;
  config_placement?: string;
  control_flow?: string;
  why_not_smaller?: string;
  verify_command?: string;
};

export type ActivePlan = {
  planId: string;
  summary: string;
  mode: "plan" | "agent";
  awaitingConfirm?: boolean;
  tasks: PlanTask[];
  shapeContract?: ShapeContract | null;
};

export type PlanConfirmState = {
  planId: string;
  sessionId: string;
  summary: string;
  tasks: PlanTask[];
  shapeContract?: ShapeContract | null;
};
