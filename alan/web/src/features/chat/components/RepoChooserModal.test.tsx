import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RepoRegistryEntry } from "../../../lib/fold";
import RepoChooserModal from "./RepoChooserModal";

const REPOS: RepoRegistryEntry[] = [
  { key: "DejvMatikar/alan", title: "Alan", posture: null },
  { key: "acme-org/widgets", title: "Widgets", posture: "autonomous" },
];

function containerOf(dialog: HTMLElement): HTMLElement {
  const container = dialog.querySelector("div");
  if (container === null) throw new Error("modal container div not found");
  return container;
}

describe("RepoChooserModal", () => {
  it("renders a dialog with an option per repository, each showing its title and repo key", () => {
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    const alan = screen.getByTestId("repo-option-DejvMatikar/alan");
    expect(alan).toHaveTextContent("Alan");
    expect(alan).toHaveTextContent("DejvMatikar/alan");
    const widgets = screen.getByTestId("repo-option-acme-org/widgets");
    expect(widgets).toHaveTextContent("Widgets");
    expect(widgets).toHaveTextContent("acme-org/widgets");
  });

  it("shows a default description when none is supplied", () => {
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={vi.fn()} />);
    expect(
      screen.getByText("Choose which repository this conversation is for."),
    ).toBeInTheDocument();
  });

  it("shows a caller-supplied consequence description in place of the default", () => {
    render(
      <RepoChooserModal
        repos={REPOS}
        description="Choosing a repository starts a build conversation for it."
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(
      screen.getByText("Choosing a repository starts a build conversation for it."),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Choose which repository this conversation is for."),
    ).not.toBeInTheDocument();
  });

  it("close button calls onClose", async () => {
    const onClose = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={onClose} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("modal-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the dialog backdrop calls onClose", async () => {
    const onClose = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={onClose} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("repo-chooser-modal"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking inside the modal content does not call onClose", async () => {
    const onClose = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={onClose} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Choose a repository"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("a single click on a repository selects it immediately", async () => {
    const onSelect = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={onSelect} onClose={vi.fn()} />);
    const user = userEvent.setup();
    await user.click(screen.getByTestId("repo-option-acme-org/widgets"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith("acme-org/widgets");
  });

  it("Enter key on a focused option selects it", async () => {
    const onSelect = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={onSelect} onClose={vi.fn()} />);
    const user = userEvent.setup();
    const item = screen.getByTestId("repo-option-DejvMatikar/alan");
    item.focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith("DejvMatikar/alan");
  });

  it("Space key on a focused option selects it", async () => {
    const onSelect = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={onSelect} onClose={vi.fn()} />);
    const user = userEvent.setup();
    const item = screen.getByTestId("repo-option-acme-org/widgets");
    item.focus();
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith("acme-org/widgets");
  });

  it("Escape key calls onClose", () => {
    const onClose = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={onClose} />);
    const container = containerOf(screen.getByTestId("repo-chooser-modal"));
    fireEvent.keyDown(container, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("the native dialog cancel event calls onClose", () => {
    const onClose = vi.fn();
    render(<RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={onClose} />);
    fireEvent(screen.getByTestId("repo-chooser-modal"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(
      <RepoChooserModal repos={REPOS} onSelect={vi.fn()} onClose={vi.fn()} />,
    );
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });
});
