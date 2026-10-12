import { describe, expect, it } from "vitest";

import { ApiError, type WorkspaceStatus } from "../../../api";
import { classifyRowActionError, rowActionAriaLabel, rowActionFor } from "./rowAction";

describe("rowActionFor", () => {
  it("active → stop", () => {
    expect(rowActionFor("active").kind).toBe("stop");
  });

  it("awaiting_input → archive", () => {
    expect(rowActionFor("awaiting_input").kind).toBe("archive");
  });

  it("completed → archive", () => {
    expect(rowActionFor("completed").kind).toBe("archive");
  });

  it("archived → unarchive", () => {
    expect(rowActionFor("archived").kind).toBe("unarchive");
  });

  it("escalated → archive", () => {
    expect(rowActionFor("escalated").kind).toBe("archive");
  });

  it("paused → archive", () => {
    expect(rowActionFor("paused").kind).toBe("archive");
  });

  it("failed → archive", () => {
    expect(rowActionFor("failed").kind).toBe("archive");
  });

  it("partial → archive", () => {
    expect(rowActionFor("partial").kind).toBe("archive");
  });

  it("never resolves resume for any status — resume stays workspace-detail-only", () => {
    const statuses: readonly WorkspaceStatus[] = [
      "active",
      "awaiting_input",
      "completed",
      "archived",
      "escalated",
      "paused",
      "failed",
      "partial",
    ];
    for (const status of statuses) {
      expect(rowActionFor(status).kind).not.toBe("resume" as never);
    }
  });
});

describe("rowActionFor — icon", () => {
  it("gives stop, archive, and unarchive each their own icon", () => {
    const stopIcon = rowActionFor("active").icon;
    const archiveIcon = rowActionFor("completed").icon;
    const unarchiveIcon = rowActionFor("archived").icon;
    expect(stopIcon).not.toBe(archiveIcon);
    expect(archiveIcon).not.toBe(unarchiveIcon);
    expect(stopIcon).not.toBe(unarchiveIcon);
  });
});

describe("rowActionAriaLabel", () => {
  it("combines the resting label with the workspace title", () => {
    expect(rowActionAriaLabel(rowActionFor("active"), false, "e2e-task-1")).toBe("Stop — e2e-task-1");
  });

  it("combines the busy label with the workspace title while busy", () => {
    expect(rowActionAriaLabel(rowActionFor("active"), true, "e2e-task-1")).toBe("Stopping… — e2e-task-1");
  });

  it("keeps label, busyLabel, and description unchanged for every action", () => {
    expect(rowActionFor("active")).toMatchObject({
      label: "Stop",
      busyLabel: "Stopping…",
      description: "Stop this workspace's running job.",
    });
    expect(rowActionFor("completed")).toMatchObject({
      label: "Archive",
      busyLabel: "Archiving…",
      description: "Archive this workspace.",
    });
    expect(rowActionFor("archived")).toMatchObject({
      label: "Unarchive",
      busyLabel: "Unarchiving…",
      description: "Unarchive this workspace.",
    });
  });
});

describe("classifyRowActionError", () => {
  it("classifies a 404 ApiError as benign", () => {
    expect(classifyRowActionError(new ApiError(404, "not found"))).toBe("benign");
  });

  it("classifies a 409 ApiError as benign", () => {
    expect(classifyRowActionError(new ApiError(409, "conflict"))).toBe("benign");
  });

  it("classifies a 500 ApiError as failed", () => {
    expect(classifyRowActionError(new ApiError(500, "boom"))).toBe("failed");
  });

  it("classifies a non-ApiError as failed", () => {
    expect(classifyRowActionError(new Error("boom"))).toBe("failed");
  });
});
