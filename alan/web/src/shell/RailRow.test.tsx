import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";

import type { DraftRow } from "../components/ChatDraftRows";
import { server } from "../test/server";
import RailRow, { railRowPath } from "./RailRow";

const BUILD_ROW: DraftRow = {
  sessionId: "11111111-1111-1111-1111-111111111111",
  createdAt: "2026-05-20T09:00:00Z",
  lastMessagePreview: "build message",
  inFlight: false,
  summaryReady: true,
  family: "build",
  fineType: "Build",
  consultantKey: null,
  engineKey: "acme/widget",
};

const EXPLORE_ROW: DraftRow = {
  sessionId: "22222222-2222-2222-2222-222222222222",
  createdAt: "2026-05-20T08:00:00Z",
  lastMessagePreview: "",
  inFlight: true,
  summaryReady: false,
  family: "explore",
  fineType: "brainstorming",
  consultantKey: "brainstorming",
  engineKey: "acme/widget",
};

function renderRow(
  row: DraftRow,
  active = false,
  onDeleted: (id: string, wasActive: boolean) => void = () => undefined,
) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route
          path="/"
          element={
            <ul>
              <RailRow row={row} engineKey="acme/widget" active={active} onDeleted={onDeleted} />
            </ul>
          }
        />
        <Route path="/repos/:owner/:repo/build" element={<div data-testid="build-page">build</div>} />
        <Route
          path="/repos/:owner/:repo/explore/:consultant"
          element={<div data-testid="explore-page">explore</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("railRowPath", () => {
  it("builds a scoped build-chat link carrying ?s=", () => {
    expect(railRowPath("acme/widget", BUILD_ROW)).toBe(
      "/repos/acme/widget/build?s=11111111-1111-1111-1111-111111111111",
    );
  });

  it("builds a scoped explore-chat link carrying the consultant and ?s=", () => {
    expect(railRowPath("acme/widget", EXPLORE_ROW)).toBe(
      "/repos/acme/widget/explore/brainstorming?s=22222222-2222-2222-2222-222222222222",
    );
  });

  it("falls back to an empty consultant segment when a row somehow carries none", () => {
    expect(railRowPath("acme/widget", { ...EXPLORE_ROW, consultantKey: null })).toBe(
      "/repos/acme/widget/explore/?s=22222222-2222-2222-2222-222222222222",
    );
  });
});

describe("RailRow — two-line layout", () => {
  it("renders the date, kind, and state on the first line", () => {
    renderRow(BUILD_ROW);
    expect(screen.getByTestId(`rail-row-date-${BUILD_ROW.sessionId}`)).toHaveTextContent("May");
    expect(screen.getByTestId(`rail-row-kind-${BUILD_ROW.sessionId}`)).toHaveTextContent("Build");
    expect(screen.getByTestId(`rail-row-state-${BUILD_ROW.sessionId}`)).toHaveTextContent(
      "Summary ready",
    );
  });

  it("omits the state label when the row has none", () => {
    renderRow({ ...BUILD_ROW, inFlight: false, summaryReady: false });
    expect(screen.queryByTestId(`rail-row-state-${BUILD_ROW.sessionId}`)).not.toBeInTheDocument();
  });

  it("prefers the in-progress state over summary-ready", () => {
    renderRow({ ...EXPLORE_ROW, inFlight: true, summaryReady: true });
    expect(screen.getByTestId(`rail-row-state-${EXPLORE_ROW.sessionId}`)).toHaveTextContent(
      "In progress",
    );
  });

  it("shows the description wrapping on the second line, clamped rather than truncated to one line", () => {
    renderRow(BUILD_ROW);
    const description = screen.getByTestId(`rail-row-description-${BUILD_ROW.sessionId}`);
    expect(description).toHaveTextContent("build message");
    expect(description.className).toContain("line-clamp-2");
    expect(description.className).not.toContain("truncate");
  });

  it("shows a placeholder description when the preview is empty", () => {
    renderRow(EXPLORE_ROW);
    expect(screen.getByText("(no messages yet)")).toBeInTheDocument();
  });

  it("shows the raw consultant key until the consultant list loads, then switches to the proper label", async () => {
    let resolveConsultants!: () => void;
    const consultantsPromise = new Promise<Response>((res) => {
      resolveConsultants = () =>
        res(
          HttpResponse.json([
            { key: "brainstorming", label: "Brainstorming", description: "Diverge.", team: "brainstorming" },
          ]),
        );
    });
    server.use(http.get("/api/explore-chat/consultants", () => consultantsPromise));
    renderRow(EXPLORE_ROW);
    expect(screen.getByTestId(`rail-row-kind-${EXPLORE_ROW.sessionId}`)).toHaveTextContent(
      "brainstorming",
    );
    resolveConsultants();
    await waitFor(() =>
      expect(screen.getByTestId(`rail-row-kind-${EXPLORE_ROW.sessionId}`)).toHaveTextContent(
        "Brainstorming",
      ),
    );
  });
});

describe("RailRow — hover and layout", () => {
  it("vertically centres the delete control against the row via absolute positioning, not the first line", () => {
    renderRow(BUILD_ROW);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    const del = screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`);
    expect(li.className).toContain("relative");
    expect(del.className).toContain("absolute");
    expect(del.className).toContain("top-1/2");
    expect(del.className).toContain("-translate-y-1/2");
  });

  it("puts the hover treatment on the <li> so it highlights the delete strip too, leaving only the focus ring on the link", () => {
    renderRow(BUILD_ROW);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    const link = screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`);
    expect(li.className).toContain("hover:bg-surface-elevated");
    expect(link.className).not.toContain("hover:bg-surface-elevated");
    expect(link.className).toContain("focus-visible:ring-text-primary/60");
  });

  it("reserves right padding on the link so its text never renders under the absolutely positioned delete control", () => {
    renderRow(BUILD_ROW);
    const link = screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`);
    expect(link.className).toContain("pr-12");
  });

  it("keeps the two-line, line-clamped description layout unaffected by the centred delete control", () => {
    renderRow(BUILD_ROW);
    const description = screen.getByTestId(`rail-row-description-${BUILD_ROW.sessionId}`);
    expect(description.className).toContain("line-clamp-2");
  });
});

describe("RailRow — selection treatment", () => {
  it("marks the active row with aria-current=true and the raised-surface background, no colour-only cue", () => {
    renderRow(BUILD_ROW, true);
    const link = screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`);
    expect(link).toHaveAttribute("aria-current", "true");
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).toMatch(/(?:^|\s)bg-surface-elevated(?:\s|$)/);
  });

  it("leaves an inactive row with no aria-current and no raised-surface background", () => {
    renderRow(BUILD_ROW, false);
    const link = screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`);
    expect(link).not.toHaveAttribute("aria-current");
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).not.toMatch(/(?:^|\s)bg-surface-elevated(?:\s|$)/);
  });

  it("carries no accent edge or accent-coloured preview text on the selected row — those cues are gone", () => {
    renderRow(BUILD_ROW, true);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).not.toContain("shadow-[inset");
    const description = screen.getByTestId(`rail-row-description-${BUILD_ROW.sessionId}`);
    expect(description.className).not.toContain("--accent-fg");
    expect(description.className).toContain("text-text-primary");
  });

  it("renders the selected and unselected row's preview text in the same neutral colour", () => {
    renderRow(BUILD_ROW, true);
    const selectedDescription = screen.getByTestId(`rail-row-description-${BUILD_ROW.sessionId}`);
    expect(selectedDescription.className).toContain("text-text-primary");
  });

  it("carries none of the selected-row cues on an unselected row, just the always-on hover treatment", () => {
    renderRow(BUILD_ROW, false);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).not.toMatch(/(?:^|\s)bg-surface-elevated(?:\s|$)/);
    expect(li.className).toContain("hover:bg-surface-elevated");
    const description = screen.getByTestId(`rail-row-description-${BUILD_ROW.sessionId}`);
    expect(description.className).not.toContain("--accent-fg");
    expect(description.className).toContain("text-text-primary");
  });

  it("navigates to the scoped chat route on click", async () => {
    const user = userEvent.setup();
    renderRow(BUILD_ROW);
    await user.click(screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`));
    expect(await screen.findByTestId("build-page")).toBeInTheDocument();
  });
});

describe("RailRow — full-width rail alignment", () => {
  it("carries no left inset on the link so its text starts flush with the row's own left edge", () => {
    renderRow(BUILD_ROW);
    const link = screen.getByTestId(`rail-row-link-${BUILD_ROW.sessionId}`);
    expect(link.className).not.toContain("px-3");
  });

  it("positions the delete control flush with the row's right edge", () => {
    renderRow(BUILD_ROW);
    const del = screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`);
    expect(del.className).toContain("right-0");
    expect(del.className).not.toContain("right-2");
  });

  it("keeps the delete error message clear of the delete control via right padding rather than left padding", async () => {
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_ROW.sessionId}`, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    renderRow(BUILD_ROW);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    const alert = await screen.findByRole("alert");
    expect(alert.className).toContain("pr-12");
    expect(alert.className).not.toContain("px-3");
  });
});

describe("RailRow — no row separator", () => {
  it("carries no border-b separator classes on an unselected row", () => {
    renderRow(BUILD_ROW, false);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).not.toContain("border-b");
    expect(li.className).not.toContain("last:border-b-0");
  });

  it("carries no border-b separator classes on a selected row", () => {
    renderRow(BUILD_ROW, true);
    const li = screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`);
    expect(li.className).not.toContain("border-b");
    expect(li.className).not.toContain("last:border-b-0");
  });
});

describe("RailRow — always-visible delete control", () => {
  it("meets the 44px minimum tap target and carries no hover-only reveal class", () => {
    renderRow(BUILD_ROW);
    const del = screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`);
    expect(del.className).toContain("min-h-11");
    expect(del.className).toContain("min-w-11");
    expect(del.className).not.toContain("opacity-0");
    expect(del.className).not.toContain("group-hover");
  });

  it("has an accessible name naming what will be deleted", () => {
    renderRow(BUILD_ROW);
    expect(screen.getByRole("button", { name: "Delete Build chat" })).toBeInTheDocument();
  });

  it("opens a confirmation dialog on click and calls no API", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_ROW.sessionId}`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderRow(BUILD_ROW);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });

  it("confirming an inactive row's delete notifies onDeleted with wasActive=false", async () => {
    let notified: [string, boolean] | null = null;
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_ROW.sessionId}`, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const user = userEvent.setup();
    renderRow(BUILD_ROW, false, (id, wasActive) => {
      notified = [id, wasActive];
    });
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(notified).toEqual([BUILD_ROW.sessionId, false]));
  });

  it("confirming the active row's delete notifies onDeleted with wasActive=true", async () => {
    let notified: [string, boolean] | null = null;
    server.use(
      http.delete(`/api/explore-chat/sessions/${EXPLORE_ROW.sessionId}`, () =>
        new HttpResponse(null, { status: 204 }),
      ),
    );
    const user = userEvent.setup();
    renderRow(EXPLORE_ROW, true, (id, wasActive) => {
      notified = [id, wasActive];
    });
    await user.click(screen.getByTestId(`rail-row-delete-${EXPLORE_ROW.sessionId}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(notified).toEqual([EXPLORE_ROW.sessionId, true]));
  });

  it("shows an inline alert and keeps the row when delete fails", async () => {
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_ROW.sessionId}`, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 }),
      ),
    );
    const user = userEvent.setup();
    renderRow(BUILD_ROW);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`));
    await user.click(screen.getByRole("button", { name: "Delete chat" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("403");
    expect(screen.getByTestId(`rail-row-${BUILD_ROW.sessionId}`)).toBeInTheDocument();
  });

  it("declining the confirmation leaves the row untouched", async () => {
    let deleteCalls = 0;
    server.use(
      http.delete(`/api/build-chat/sessions/${BUILD_ROW.sessionId}`, () => {
        deleteCalls += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const user = userEvent.setup();
    renderRow(BUILD_ROW);
    await user.click(screen.getByTestId(`rail-row-delete-${BUILD_ROW.sessionId}`));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(deleteCalls).toBe(0);
  });
});
