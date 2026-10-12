import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { server } from "../test/server";
import ConsultantModal from "./ConsultantModal";

const CONSULTANTS = [
  { key: "brainstorming", label: "Brainstorming", description: "Diverge.", team: "brainstorming" },
  { key: "research", label: "Research", description: "Research.", team: "research" },
  { key: "architecture", label: "Architecture", description: "Architecture.", team: "architecture" },
  { key: "opportunity_scout", label: "Opportunity scout", description: "Scout.", team: "opportunity_scout" },
];

function ExploreRouteProbe() {
  const [params] = useSearchParams();
  return <div data-testid="explore-page">{params.get("engine") ?? "none"}</div>;
}

function LauncherAndModal() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" data-testid="launcher" onClick={() => setOpen(true)}>
        Explore
      </button>
      {open && <ConsultantModal onClose={() => setOpen(false)} repoKey={null} />}
    </>
  );
}

function renderModal(onClose = vi.fn()) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={
            <>
              <ConsultantModal onClose={onClose} repoKey={null} />
            </>
          }
        />
        <Route
          path="/explore/:consultant"
          element={<div data-testid="explore-page">explore</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function containerOf(dialog: HTMLElement): HTMLElement {
  const container = dialog.querySelector("div");
  if (container === null) throw new Error("modal container div not found");
  return container;
}

describe("ConsultantModal", () => {
  it("renders a dialog with every served consultant", async () => {
    renderModal();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    for (const c of CONSULTANTS) {
      expect(await screen.findByTestId(`consultant-${c.key}`)).toBeInTheDocument();
    }
  });

  it("shows the description for each served consultant", async () => {
    renderModal();
    for (const c of CONSULTANTS) {
      expect(await screen.findByText(c.description)).toBeInTheDocument();
    }
  });

  it("shows a modal-level description of what choosing a consultant does", () => {
    renderModal();
    expect(
      screen.getByText(
        "Choosing a consultant opens a conversation with them; nothing else starts automatically.",
      ),
    ).toBeInTheDocument();
  });

  it("close button calls onClose and uses the shared ghost button styling", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    const closeButton = screen.getByTestId("modal-close");
    expect(closeButton.className).toContain("border-border");
    expect(closeButton.className).toContain("text-text-primary");
    await user.click(closeButton);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the dialog backdrop (outside the content) calls onClose", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("consultant-modal"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking inside the modal content does not call onClose", async () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const user = userEvent.setup();
    await user.click(screen.getByText("Choose a consultant"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a single click on a consultant navigates to the explore route", async () => {
    renderModal();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("consultant-architecture"));
    expect(await screen.findByTestId("explore-page")).toBeInTheDocument();
  });

  it("with no repoKey, navigating to the explore route carries no ?engine=", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<ConsultantModal onClose={vi.fn()} repoKey={null} />} />
          <Route path="/explore/:consultant" element={<ExploreRouteProbe />} />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("consultant-architecture"));
    expect(await screen.findByTestId("explore-page")).toHaveTextContent("none");
  });

  it("a given repoKey navigates directly to the nested scoped explore route", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<ConsultantModal onClose={vi.fn()} repoKey="acme-org/widgets" />} />
          <Route
            path="/repos/:owner/:repo/explore/:consultant"
            element={<div data-testid="explore-page">explore</div>}
          />
        </Routes>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("consultant-architecture"));
    expect(await screen.findByTestId("explore-page")).toBeInTheDocument();
  });

  it("Enter key on focused item navigates to the chat", async () => {
    renderModal();
    const user = userEvent.setup();
    const item = await screen.findByTestId("consultant-research");
    item.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByTestId("explore-page")).toBeInTheDocument();
  });

  it("Space key on focused item navigates to the chat", async () => {
    renderModal();
    const user = userEvent.setup();
    const item = await screen.findByTestId("consultant-opportunity_scout");
    item.focus();
    await user.keyboard(" ");
    expect(await screen.findByTestId("explore-page")).toBeInTheDocument();
  });

  it("shows the ui_review consultant when the served list carries it", async () => {
    server.use(
      http.get("/api/explore-chat/consultants", () =>
        HttpResponse.json([
          ...CONSULTANTS,
          { key: "ui_review", label: "UI review", description: "Review.", team: "ui_review" },
        ]),
      ),
    );
    renderModal();
    expect(await screen.findByTestId("consultant-ui_review")).toBeInTheDocument();
  });

  it("pressing a non-Enter/Space key does not navigate", async () => {
    renderModal();
    const user = userEvent.setup();
    const item = await screen.findByTestId("consultant-brainstorming");
    item.focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.queryByTestId("explore-page")).not.toBeInTheDocument();
  });

  it("shows the shared load-error banner and no consultant items when the list fails to load", async () => {
    server.use(
      http.get("/api/explore-chat/consultants", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );
    renderModal();
    expect(await screen.findByTestId("consultants-load-error")).toBeInTheDocument();
    for (const c of CONSULTANTS) {
      expect(screen.queryByTestId(`consultant-${c.key}`)).not.toBeInTheDocument();
    }
  });

  it("Escape key closes the modal", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    const container = containerOf(screen.getByTestId("consultant-modal"));
    fireEvent.keyDown(container, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("the native dialog cancel event closes the modal", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    fireEvent(screen.getByTestId("consultant-modal"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = renderModal();
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });

  it("focus returns to the launching control once the modal closes", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <LauncherAndModal />
      </MemoryRouter>,
    );
    const launcher = screen.getByTestId("launcher");
    const user = userEvent.setup();
    await user.click(launcher);
    expect(launcher).toHaveFocus();

    const container = containerOf(screen.getByTestId("consultant-modal"));
    fireEvent.keyDown(container, { key: "Escape" });

    expect(screen.queryByTestId("consultant-modal")).not.toBeInTheDocument();
    expect(launcher).toHaveFocus();
  });
});
