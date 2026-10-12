import { useCallback, useEffect, useRef, useState } from "react";

import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "../../../test/server";
import StickyInstructionCard from "./StickyInstructionCard";

const noop = (): void => undefined;

function Harness({ taskId }: Readonly<{ taskId: string }>) {
  const [cleared, setCleared] = useState(false);
  const noticeRef = useRef<HTMLOutputElement>(null);
  const handleCleared = useCallback(() => {
    setCleared(true);
  }, []);
  useEffect(() => {
    if (cleared) noticeRef.current?.focus();
  }, [cleared]);
  return (
    <>
      <StickyInstructionCard taskId={taskId} onCleared={handleCleared} />
      {cleared && (
        <output ref={noticeRef} tabIndex={-1} data-testid="cleared-notice">
          Persisted instruction cleared.
        </output>
      )}
    </>
  );
}

describe("StickyInstructionCard", () => {
  it("renders nothing when there is no persisted instruction and no error", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: null, origin: null }),
      ),
    );
    const { container } = render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("renders nothing while the fetch is pending", () => {
    server.use(
      http.get(
        "/api/workspaces/t1/sticky-instructions",
        () => new Promise<Response>(() => undefined),
      ),
    );
    const { container } = render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows text, origin suffix, and a Clear control when populated", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    expect(await screen.findByTestId("sticky-instruction-card")).toBeInTheDocument();
    expect(screen.getByTestId("sticky-instruction-text")).toHaveTextContent("focus here");
    expect(screen.getByTestId("sticky-instruction-origin")).toHaveTextContent(
      "typed for a prior dispatch",
    );
    expect(screen.getByTestId("sticky-instruction-clear")).toBeInTheDocument();
  });

  it("omits the origin suffix when origin is null", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: null }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    await screen.findByTestId("sticky-instruction-card");
    expect(screen.queryByTestId("sticky-instruction-origin")).not.toBeInTheDocument();
  });

  it("shows a block error instead of silently hiding a real persisted instruction", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    const error = await screen.findByTestId("sticky-instruction-error");
    expect(error).toHaveAttribute("role", "alert");
    expect(error).toHaveTextContent("boom");
  });

  it("a failed Clear leaves the text intact and shows a clear error", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    await screen.findByTestId("sticky-instruction-card");
    await user.click(screen.getByTestId("sticky-instruction-clear"));
    const error = await screen.findByTestId("sticky-instruction-clear-error");
    expect(error).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("sticky-instruction-text")).toHaveTextContent("focus here");
  });

  it("stringifies a non-Error rejection from clear() into the clear-error state", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    const apiModule = await import("../../../api");
    const original = apiModule.api.clearStickyInstructions;
    apiModule.api.clearStickyInstructions = () =>
      // eslint-disable-next-line @typescript-eslint/prefer-promise-reject-errors
      Promise.reject("plain-clear-error");
    try {
      render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
      await screen.findByTestId("sticky-instruction-card");
      await user.click(screen.getByTestId("sticky-instruction-clear"));
      expect(await screen.findByTestId("sticky-instruction-clear-error")).toHaveTextContent(
        "plain-clear-error",
      );
    } finally {
      apiModule.api.clearStickyInstructions = original;
    }
  });

  it("Clear unmounts the card and hands focus to the parent-hosted announcement", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete(
        "/api/workspaces/t1/sticky-instructions",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );
    render(<Harness taskId="t1" />);
    await screen.findByTestId("sticky-instruction-card");
    await user.click(screen.getByTestId("sticky-instruction-clear"));
    const notice = await screen.findByTestId("cleared-notice");
    expect(screen.queryByTestId("sticky-instruction-card")).not.toBeInTheDocument();
    await waitFor(() => expect(notice).toHaveFocus());
  });

  it("the Clear control is reachable via Tab", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    const clearButton = await screen.findByTestId("sticky-instruction-clear");
    await user.tab();
    expect(clearButton).toHaveFocus();
  });

  it("names the Clear control by what it clears", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    const button = await screen.findByTestId("sticky-instruction-clear");
    expect(button).toHaveAccessibleName("Clear instruction");
    expect(button).toHaveTextContent("Clear");
  });

  it("describes the consequence of Clear on keyboard focus", async () => {
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    const button = await screen.findByTestId("sticky-instruction-clear");
    act(() => {
      button.focus();
    });
    expect(button).toHaveAccessibleDescription(
      "Stops this instruction from applying to future automatically-routed runs. It cannot be restored.",
    );
  });

  it("stays focusable and reports aria-busy with a busy accessible name while clearing", async () => {
    const user = userEvent.setup();
    let resolveClear: (() => void) | undefined;
    server.use(
      http.get("/api/workspaces/t1/sticky-instructions", () =>
        HttpResponse.json({ text: "focus here", origin: "dispatch" }),
      ),
      http.delete(
        "/api/workspaces/t1/sticky-instructions",
        () =>
          new Promise<Response>((resolve) => {
            resolveClear = () => resolve(new HttpResponse(null, { status: 204 }));
          }),
      ),
    );
    render(<StickyInstructionCard taskId="t1" onCleared={noop} />);
    const button = await screen.findByTestId("sticky-instruction-clear");
    void user.click(button);
    await waitFor(() => expect(button).toHaveAttribute("aria-busy", "true"));
    expect(button).toHaveAccessibleName("Clearing instruction…");
    expect(button).not.toBeDisabled();
    resolveClear?.();
  });
});
