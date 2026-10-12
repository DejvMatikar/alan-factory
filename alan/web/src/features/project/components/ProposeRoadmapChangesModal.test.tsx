import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type { RoadmapItemView } from "../../../api";
import { server } from "../../../test/server";
import ProposeRoadmapChangesModal from "./ProposeRoadmapChangesModal";

function item(overrides: Partial<RoadmapItemView> = {}): RoadmapItemView {
  return {
    id: "itm_1",
    title: "Ship the widget",
    text: "Build it end to end.",
    order: 0,
    status: "not_started",
    merged_prs: [],
    ...overrides,
  };
}

const ITEMS: RoadmapItemView[] = [
  item({ id: "itm_1", title: "Ship the widget", order: 0 }),
  item({ id: "itm_2", title: "Polish the dashboard", text: "", order: 1 }),
];

const REORDER_URL = "/api/projects/alpha/roadmap/reorder";
const editUrl = (id: string) => `/api/projects/alpha/roadmap/items/${id}`;

describe("ProposeRoadmapChangesModal", () => {
  it("shows the dialog on mount defaulting to Reorder, with items ordered by position", () => {
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    expect(screen.getByTestId("propose-roadmap-changes-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("propose-roadmap-mode-select")).toHaveValue("reorder");
    const list = screen.getByTestId("propose-roadmap-reorder-list");
    const rows = within(list).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Ship the widget");
    expect(rows[1]).toHaveTextContent("Polish the dashboard");
  });

  it("disables Up on the first item and Down on the last item, and enables the other direction", () => {
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    expect(screen.getByTestId("propose-roadmap-reorder-up-itm_1")).toBeDisabled();
    expect(screen.getByTestId("propose-roadmap-reorder-down-itm_1")).not.toBeDisabled();
    expect(screen.getByTestId("propose-roadmap-reorder-up-itm_2")).not.toBeDisabled();
    expect(screen.getByTestId("propose-roadmap-reorder-down-itm_2")).toBeDisabled();
  });

  it("moves an item and proposes the new order", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post(REORDER_URL, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          { fingerprint: "fp1", project_id: "alpha", proposed_at: "2026-07-08T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const onProposed = vi.fn();
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={onProposed}
      />,
    );
    await user.click(screen.getByTestId("propose-roadmap-reorder-down-itm_1"));
    const list = screen.getByTestId("propose-roadmap-reorder-list");
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("Polish the dashboard");
    await user.click(screen.getByTestId("propose-roadmap-reorder-up-itm_1"));
    expect(within(list).getAllByRole("listitem")[0]).toHaveTextContent("Ship the widget");
    await user.click(screen.getByTestId("propose-roadmap-reorder-down-itm_1"));
    await user.click(screen.getByTestId("propose-roadmap-submit"));

    await waitFor(() => expect(onProposed).toHaveBeenCalledOnce());
    expect(capturedBody).toEqual({ ordered_ids: ["itm_2", "itm_1"] });
  });

  it("switches to Edit mode, prefilling the first item's title/text", async () => {
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByTestId("propose-roadmap-mode-select"), "edit");
    expect(screen.getByTestId("propose-roadmap-edit-item-select")).toHaveValue("itm_1");
    expect(screen.getByTestId("propose-roadmap-edit-title")).toHaveValue("Ship the widget");
    expect(screen.getByTestId("propose-roadmap-edit-text")).toHaveValue("Build it end to end.");
  });

  it("selecting a different item refills title/text from that item", async () => {
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByTestId("propose-roadmap-mode-select"), "edit");
    await user.selectOptions(screen.getByTestId("propose-roadmap-edit-item-select"), "itm_2");
    expect(screen.getByTestId("propose-roadmap-edit-title")).toHaveValue("Polish the dashboard");
    expect(screen.getByTestId("propose-roadmap-edit-text")).toHaveValue("");
  });

  it("disables Propose in edit mode until the title is non-blank", async () => {
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByTestId("propose-roadmap-mode-select"), "edit");
    await user.clear(screen.getByTestId("propose-roadmap-edit-title"));
    expect(screen.getByTestId("propose-roadmap-submit")).toBeDisabled();
    await user.type(screen.getByTestId("propose-roadmap-edit-title"), "New title");
    expect(screen.getByTestId("propose-roadmap-submit")).not.toBeDisabled();
  });

  it("proposes an edit with the typed title/text", async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post(editUrl("itm_1"), async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          { fingerprint: "fp2", project_id: "alpha", proposed_at: "2026-07-08T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const onProposed = vi.fn();
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={onProposed}
      />,
    );
    await user.selectOptions(screen.getByTestId("propose-roadmap-mode-select"), "edit");
    await user.clear(screen.getByTestId("propose-roadmap-edit-text"));
    await user.type(screen.getByTestId("propose-roadmap-edit-text"), "Updated body.");
    await user.click(screen.getByTestId("propose-roadmap-submit"));

    await waitFor(() => expect(onProposed).toHaveBeenCalledOnce());
    expect(capturedBody).toEqual({ title: "Ship the widget", text: "Updated body." });
  });

  it("shows an inline error and keeps the dialog open when reordering fails", async () => {
    server.use(
      http.post(REORDER_URL, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.click(screen.getByTestId("propose-roadmap-submit"));
    const alert = await screen.findByTestId("propose-roadmap-error");
    expect(alert).toHaveTextContent("500");
    expect(screen.getByTestId("propose-roadmap-changes-dialog")).toBeInTheDocument();
  });

  it("shows an inline error and keeps the dialog open when editing fails", async () => {
    server.use(
      http.post(editUrl("itm_1"), () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.selectOptions(screen.getByTestId("propose-roadmap-mode-select"), "edit");
    await user.click(screen.getByTestId("propose-roadmap-submit"));
    const alert = await screen.findByTestId("propose-roadmap-error");
    expect(alert).toHaveTextContent("500");
  });

  it("disables submit and shows no rows when rendered with an empty item list", () => {
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={[]}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    expect(screen.getByTestId("propose-roadmap-reorder-list")).toBeEmptyDOMElement();
    expect(screen.getByTestId("propose-roadmap-submit")).toBeDisabled();
  });

  it("Cancel button closes the modal", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={onClose}
        onProposed={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Escape closes the modal when idle", () => {
    const onClose = vi.fn();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={onClose}
        onProposed={vi.fn()}
      />,
    );
    const form = screen.getByTestId("propose-roadmap-changes-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Ctrl+Enter submits the modal", async () => {
    server.use(
      http.post(REORDER_URL, () =>
        HttpResponse.json(
          { fingerprint: "fp3", project_id: "alpha", proposed_at: "" },
          { status: 201 },
        ),
      ),
    );
    const onProposed = vi.fn();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={onProposed}
      />,
    );
    const form = screen.getByTestId("propose-roadmap-changes-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(onProposed).toHaveBeenCalledOnce());
  });

  it("ignores Ctrl+Enter when nothing can be submitted", () => {
    const onProposed = vi.fn();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={[]}
        onClose={vi.fn()}
        onProposed={onProposed}
      />,
    );
    const form = screen.getByTestId("propose-roadmap-changes-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(onProposed).not.toHaveBeenCalled();
  });

  it("ignores a plain form submit event", () => {
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    const form = screen.getByTestId("propose-roadmap-changes-dialog").querySelector("form")!;
    fireEvent.submit(form);
    expect(screen.getByTestId("propose-roadmap-changes-dialog")).toBeInTheDocument();
  });

  it("blocks Escape and Ctrl+Enter while a propose request is in flight", async () => {
    server.use(
      http.post(REORDER_URL, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({});
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={onClose}
        onProposed={vi.fn()}
      />,
    );
    await user.click(screen.getByTestId("propose-roadmap-submit"));
    expect(screen.getByTestId("propose-roadmap-submit")).toHaveTextContent("Proposing…");

    const form = screen.getByTestId("propose-roadmap-changes-dialog").querySelector("form")!;
    fireEvent.keyDown(form, { key: "Escape" });
    fireEvent.keyDown(form, { key: "Enter", ctrlKey: true });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("blocks the dialog's native cancel event while a propose request is in flight", async () => {
    server.use(
      http.post(REORDER_URL, async () => {
        await new Promise(() => undefined);
        return HttpResponse.json({});
      }),
    );
    const user = userEvent.setup();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    await user.click(screen.getByTestId("propose-roadmap-submit"));
    const dialog = screen.getByTestId("propose-roadmap-changes-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(screen.getByTestId("propose-roadmap-changes-dialog")).toBeInTheDocument();
  });

  it("the dialog's native cancel event closes the modal when idle", () => {
    const onClose = vi.fn();
    render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={onClose}
        onProposed={vi.fn()}
      />,
    );
    const dialog = screen.getByTestId("propose-roadmap-changes-dialog");
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("shows the dialog on mount and closes it on unmount", () => {
    const showModal = vi.spyOn(HTMLDialogElement.prototype, "showModal");
    const close = vi.spyOn(HTMLDialogElement.prototype, "close");
    const { unmount } = render(
      <ProposeRoadmapChangesModal
        projectId="alpha"
        items={ITEMS}
        onClose={vi.fn()}
        onProposed={vi.fn()}
      />,
    );
    expect(showModal).toHaveBeenCalled();
    unmount();
    expect(close).toHaveBeenCalled();
  });
});
