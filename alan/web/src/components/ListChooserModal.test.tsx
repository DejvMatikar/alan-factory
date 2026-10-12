import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import ListChooserModal from "./ListChooserModal";

interface Item {
  id: string;
  label: string;
}

const ITEMS: Item[] = [
  { id: "a", label: "Alpha" },
  { id: "b", label: "Bravo" },
];

function renderModal(props: {
  description?: string | undefined;
  onSelect?: ((item: Item) => void) | undefined;
  onClose?: (() => void) | undefined;
} = {}) {
  const onSelect = props.onSelect ?? vi.fn();
  const onClose = props.onClose ?? vi.fn();
  render(
    <ListChooserModal
      ariaLabel="Choose an item"
      testId="item-chooser-modal"
      title="Choose an item"
      description={props.description}
      items={ITEMS}
      getItemKey={(item) => item.id}
      getItemTestId={(item) => `item-option-${item.id}`}
      renderItem={(item) => <span>{item.label}</span>}
      onSelect={onSelect}
      onClose={onClose}
    />,
  );
  return { onSelect, onClose };
}

function containerOf(dialog: HTMLElement): HTMLElement {
  const container = dialog.querySelector("div");
  if (container === null) throw new Error("modal container div not found");
  return container;
}

describe("ListChooserModal", () => {
  it("renders a dialog with an option per item", () => {
    renderModal();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("item-option-a")).toHaveTextContent("Alpha");
    expect(screen.getByTestId("item-option-b")).toHaveTextContent("Bravo");
  });

  it("renders no description paragraph when none is supplied", () => {
    renderModal();
    expect(screen.queryByText(/press Enter or double-click/i)).not.toBeInTheDocument();
    expect(screen.getByRole("dialog").querySelector("p")).not.toBeInTheDocument();
  });

  it("renders the supplied description", () => {
    renderModal({ description: "Pick one to continue." });
    expect(screen.getByText("Pick one to continue.")).toBeInTheDocument();
  });

  it("a single click on a row commits the pick immediately", async () => {
    const { onSelect } = renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("item-option-a"));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(ITEMS[0]);
  });

  it("Enter key on a focused row commits the pick", async () => {
    const { onSelect } = renderModal();
    const user = userEvent.setup();
    screen.getByTestId("item-option-b").focus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(ITEMS[1]);
  });

  it("Space key on a focused row commits the pick", async () => {
    const { onSelect } = renderModal();
    const user = userEvent.setup();
    screen.getByTestId("item-option-b").focus();
    await user.keyboard(" ");
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect).toHaveBeenCalledWith(ITEMS[1]);
  });

  it("close button calls onClose", async () => {
    const { onClose } = renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("modal-close"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the dialog backdrop calls onClose", async () => {
    const { onClose } = renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByTestId("item-chooser-modal"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking inside the modal content does not call onClose", async () => {
    const { onClose } = renderModal();
    const user = userEvent.setup();
    await user.click(screen.getByText("Choose an item"));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("Escape key calls onClose", () => {
    const { onClose } = renderModal();
    const container = containerOf(screen.getByTestId("item-chooser-modal"));
    fireEvent.keyDown(container, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("the native dialog cancel event calls onClose", () => {
    const { onClose } = renderModal();
    fireEvent(screen.getByTestId("item-chooser-modal"), new Event("cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Ctrl+Enter is a no-op — no confirm path remains", () => {
    const { onSelect, onClose } = renderModal();
    const container = containerOf(screen.getByTestId("item-chooser-modal"));
    fireEvent.keyDown(container, { key: "Enter", ctrlKey: true });
    expect(onSelect).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("renders no filter input when none is supplied", () => {
    renderModal();
    expect(screen.queryByTestId("list-chooser-filter")).not.toBeInTheDocument();
  });

  it("renders a filter input when supplied, focused on open, and reports typed text", async () => {
    const onChange = vi.fn();
    render(
      <ListChooserModal
        ariaLabel="Choose an item"
        testId="item-chooser-modal"
        title="Choose an item"
        filter={{ value: "", onChange, placeholder: "Search…", ariaLabel: "Filter items" }}
        items={ITEMS}
        getItemKey={(item) => item.id}
        getItemTestId={(item) => `item-option-${item.id}`}
        renderItem={(item) => <span>{item.label}</span>}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByTestId("list-chooser-filter");
    expect(input).toHaveAccessibleName("Filter items");
    expect(input).toHaveFocus();

    const user = userEvent.setup();
    await user.type(input, "a");
    expect(onChange).toHaveBeenCalledWith("a");
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(
      <ListChooserModal
        ariaLabel="Choose an item"
        testId="item-chooser-modal"
        title="Choose an item"
        items={ITEMS}
        getItemKey={(item) => item.id}
        getItemTestId={(item) => `item-option-${item.id}`}
        renderItem={(item) => <span>{item.label}</span>}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });

  it("renders the supplied notice in place of the list, with no list items", () => {
    render(
      <ListChooserModal
        ariaLabel="Choose an item"
        testId="item-chooser-modal"
        title="Choose an item"
        notice={<p data-testid="chooser-notice">Could not load items.</p>}
        items={ITEMS}
        getItemKey={(item) => item.id}
        getItemTestId={(item) => `item-option-${item.id}`}
        renderItem={(item) => <span>{item.label}</span>}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("chooser-notice")).toHaveTextContent("Could not load items.");
    expect(screen.queryByTestId("item-option-a")).not.toBeInTheDocument();
    expect(screen.queryByTestId("item-option-b")).not.toBeInTheDocument();
  });
});
