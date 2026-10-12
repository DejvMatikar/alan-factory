import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ChatDeleteAnnouncementRegion, useChatDelete } from "./useChatDelete";

const SID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";

interface HarnessProps {
  sessionId: string | null;
  interruptCall: (id: string) => Promise<unknown>;
  deleteCall: (id: string) => Promise<unknown>;
}

function Harness({ sessionId, interruptCall, deleteCall }: Readonly<HarnessProps>) {
  const { requestDelete, deleting, deleteError, modal } = useChatDelete(
    sessionId,
    interruptCall,
    deleteCall,
  );
  return (
    <div>
      {modal}
      <button type="button" data-testid="request-delete" onClick={requestDelete}>
        delete
      </button>
      <div data-testid="deleting">{String(deleting)}</div>
      <div data-testid="delete-error">{deleteError ?? ""}</div>
    </div>
  );
}

function renderHarness(props: HarnessProps) {
  return render(
    <MemoryRouter initialEntries={["/chat"]}>
      <Routes>
        <Route path="/chat" element={<Harness {...props} />} />
        <Route path="/" element={<div data-testid="home">home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function runSharedTests(label: string) {
  describe(label, () => {
    it("requestDelete opens the modal and calls nothing", async () => {
      const interruptCall = vi.fn().mockResolvedValue(undefined);
      const deleteCall = vi.fn().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderHarness({ sessionId: SID, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(interruptCall).not.toHaveBeenCalled();
      expect(deleteCall).not.toHaveBeenCalled();
    });

    it("cancel closes the modal and calls nothing", async () => {
      const interruptCall = vi.fn().mockResolvedValue(undefined);
      const deleteCall = vi.fn().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderHarness({ sessionId: SID, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(interruptCall).not.toHaveBeenCalled();
      expect(deleteCall).not.toHaveBeenCalled();
    });

    it("confirm calls interrupt then delete in that order, then navigates home", async () => {
      const order: string[] = [];
      const interruptCall = vi.fn(() => {
        order.push("interrupt");
        return Promise.resolve();
      });
      const deleteCall = vi.fn(() => {
        order.push("delete");
        return Promise.resolve();
      });
      const user = userEvent.setup();
      renderHarness({ sessionId: SID, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      await waitFor(() => expect(screen.getByTestId("home")).toBeInTheDocument());
      expect(order).toEqual(["interrupt", "delete"]);
      expect(interruptCall).toHaveBeenCalledWith(SID);
      expect(deleteCall).toHaveBeenCalledWith(SID);
    });

    it("interrupt rejection prevents delete, surfaces an error, and does not navigate", async () => {
      const interruptCall = vi.fn(() => Promise.reject(new Error("interrupt failed")));
      const deleteCall = vi.fn().mockResolvedValue(undefined);
      const user = userEvent.setup();
      renderHarness({ sessionId: SID, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      await waitFor(() =>
        expect(screen.getByTestId("delete-error")).toHaveTextContent("interrupt failed"),
      );
      expect(deleteCall).not.toHaveBeenCalled();
      expect(screen.queryByTestId("home")).toBeNull();
    });

    it("delete rejection surfaces an error, resets deleting, and does not navigate", async () => {
      const interruptCall = vi.fn().mockResolvedValue(undefined);
      const deleteCall = vi.fn(() => Promise.reject(new Error("delete failed")));
      const user = userEvent.setup();
      renderHarness({ sessionId: SID, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      await user.click(screen.getByRole("button", { name: "Delete chat" }));
      await waitFor(() =>
        expect(screen.getByTestId("delete-error")).toHaveTextContent("delete failed"),
      );
      expect(screen.getByTestId("deleting")).toHaveTextContent("false");
      expect(screen.queryByTestId("home")).toBeNull();
    });

    it("requestDelete is inert when sessionId is null", async () => {
      const interruptCall = vi.fn();
      const deleteCall = vi.fn();
      const user = userEvent.setup();
      renderHarness({ sessionId: null, interruptCall, deleteCall });
      await user.click(screen.getByTestId("request-delete"));
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(interruptCall).not.toHaveBeenCalled();
      expect(deleteCall).not.toHaveBeenCalled();
    });
  });
}

runSharedTests("build pair");
runSharedTests("explore pair");

describe("useChatDelete — scoped route", () => {
  it("navigates to the repository dashboard, not the global home, when mounted under /repos/:owner/:repo", async () => {
    const interruptCall = vi.fn().mockResolvedValue(undefined);
    const deleteCall = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget/build"]}>
        <Routes>
          <Route
            path="/repos/:owner/:repo/build"
            element={<Harness sessionId={SID} interruptCall={interruptCall} deleteCall={deleteCall} />}
          />
          <Route path="/repos/:owner/:repo" element={<div data-testid="repo-dashboard">dashboard</div>} />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(screen.getByTestId("request-delete"));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(screen.getByTestId("repo-dashboard")).toBeInTheDocument());
  });
});

describe("ChatDeleteAnnouncementRegion — cross-subtree announcement channel", () => {
  it("announces the deletion via a region that survives the deleting component's unmount on navigation", async () => {
    const interruptCall = vi.fn().mockResolvedValue(undefined);
    const deleteCall = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/repos/acme/widget/build"]}>
        <ChatDeleteAnnouncementRegion />
        <Routes>
          <Route
            path="/repos/:owner/:repo/build"
            element={<Harness sessionId={SID} interruptCall={interruptCall} deleteCall={deleteCall} />}
          />
          <Route path="/repos/:owner/:repo" element={<div data-testid="repo-dashboard">dashboard</div>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("chat-delete-announcement")).toHaveTextContent("");
    await user.click(screen.getByTestId("request-delete"));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(screen.getByTestId("repo-dashboard")).toBeInTheDocument());
    expect(screen.getByTestId("chat-delete-announcement")).toHaveTextContent(
      "Conversation deleted",
    );
  });

  it("stops updating once unmounted, so a stale listener never sets state on a gone component", () => {
    const { unmount } = render(<ChatDeleteAnnouncementRegion />);
    expect(() => unmount()).not.toThrow();
  });
});
