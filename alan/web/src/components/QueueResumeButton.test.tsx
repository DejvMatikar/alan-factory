import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import QueueResumeButton from "./QueueResumeButton";

function renderButton() {
  const onResumed = vi.fn();
  const onFailed = vi.fn();
  render(<QueueResumeButton onResumed={onResumed} onFailed={onFailed} />);
  return { onResumed, onFailed };
}

describe("QueueResumeButton", () => {
  it("calls api.resumeQueue exactly once when clicked", async () => {
    let calls = 0;
    server.use(
      http.post("/api/queue/resume", () => {
        calls += 1;
        return HttpResponse.json({ ok: true, queue_paused: false, paused_until: null });
      }),
    );
    const user = userEvent.setup();
    renderButton();
    await user.click(screen.getByTestId("queue-resume-button"));
    await waitFor(() => {
      expect(calls).toBe(1);
    });
  });

  it("disables the button while the request is in flight", async () => {
    const releaseRef: { current: (() => void) | null } = { current: null };
    server.use(
      http.post("/api/queue/resume", async () => {
        await new Promise<void>((res) => {
          releaseRef.current = res;
        });
        return HttpResponse.json({ ok: true, queue_paused: false, paused_until: null });
      }),
    );
    const user = userEvent.setup();
    renderButton();
    const button = screen.getByTestId("queue-resume-button");
    await user.click(button);
    await waitFor(() => {
      expect(button).toBeDisabled();
    });
    releaseRef.current?.();
    await waitFor(() => {
      expect(button).not.toBeDisabled();
    });
  });

  it("fires onResumed on success", async () => {
    server.use(
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ ok: true, queue_paused: false, paused_until: null }),
      ),
    );
    const user = userEvent.setup();
    const { onResumed, onFailed } = renderButton();
    await user.click(screen.getByTestId("queue-resume-button"));
    await waitFor(() => {
      expect(onResumed).toHaveBeenCalledTimes(1);
    });
    expect(onFailed).not.toHaveBeenCalled();
  });

  it("fires onFailed and re-enables the button on rejection", async () => {
    server.use(
      http.post("/api/queue/resume", () =>
        HttpResponse.json({ detail: "control token mismatch" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    const { onResumed, onFailed } = renderButton();
    const button = screen.getByTestId("queue-resume-button");
    await user.click(button);
    await waitFor(() => {
      expect(onFailed).toHaveBeenCalledTimes(1);
    });
    expect(onResumed).not.toHaveBeenCalled();
    expect(button).not.toBeDisabled();
  });

  it("is reachable via Tab and activates on both Enter and Space", async () => {
    let calls = 0;
    server.use(
      http.post("/api/queue/resume", () => {
        calls += 1;
        return HttpResponse.json({ ok: true, queue_paused: false, paused_until: null });
      }),
    );
    const user = userEvent.setup();
    renderButton();
    await user.tab();
    const button = screen.getByTestId("queue-resume-button");
    expect(button).toHaveFocus();

    await user.keyboard("{Enter}");
    await waitFor(() => {
      expect(calls).toBe(1);
    });
    await waitFor(() => {
      expect(button).not.toBeDisabled();
    });

    button.focus();
    await user.keyboard(" ");
    await waitFor(() => {
      expect(calls).toBe(2);
    });
  });

  it("carries the shared focus-visible and tap-target baseline classes", () => {
    renderButton();
    const button = screen.getByTestId("queue-resume-button");
    expect(button.className).toContain("focus-visible:ring-2");
    expect(button.className).toContain("min-h-11");
    expect(button.className).toContain("min-w-11");
  });
});
