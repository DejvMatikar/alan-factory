import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { ACCENT_COLORS, type AccentChoice, type EngineSummary } from "../../../api";
import { makeEngineSummary } from "../../../test/engineFixtures";
import { server } from "../../../test/server";
import AccentPicker from "./AccentPicker";

const OWNER = "acme";
const REPO = "widget";
const ENGINE_KEY = `${OWNER}/${REPO}`;

function baseEngine(accent?: AccentChoice | null): EngineSummary {
  return makeEngineSummary({
    repo: { owner: OWNER, repo: REPO },
    posture: "propose",
    ...(accent !== undefined ? { accent } : {}),
  });
}

function mockEngine(accent?: AccentChoice | null, status?: number) {
  server.use(
    http.get(`/api/engines/${OWNER}/${REPO}`, () =>
      status !== undefined
        ? HttpResponse.json({ detail: "boom" }, { status })
        : HttpResponse.json({
            repo: { owner: OWNER, repo: REPO },
            posture: "propose",
            ...(accent !== undefined ? { accent } : {}),
          }),
    ),
  );
}

function mockEngineList(engines: EngineSummary[]) {
  server.use(http.get("/api/engines", () => HttpResponse.json({ engines })));
}

function mockSaveAccent(status = 200) {
  server.use(
    http.put(`/api/engines/${OWNER}/${REPO}/accent`, async ({ request }) => {
      if (status !== 200) return HttpResponse.json({ detail: "save failed" }, { status });
      const body = (await request.json()) as { accent: AccentChoice | null };
      return HttpResponse.json({
        repo: { owner: OWNER, repo: REPO },
        posture: "propose",
        accent: body.accent,
      });
    }),
  );
}

describe("AccentPicker — loading and error", () => {
  it("shows a loading state before the engine detail resolves", () => {
    mockEngineList([baseEngine()]);
    server.use(http.get(`/api/engines/${OWNER}/${REPO}`, () => new Promise<Response>(() => undefined)));
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    expect(screen.getByTestId("accent-picker-loading")).toBeInTheDocument();
  });

  it("shows an error state when the engine detail fetch fails", async () => {
    mockEngineList([baseEngine()]);
    mockEngine(undefined, 500);
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    expect(await screen.findByTestId("accent-picker-error")).toBeInTheDocument();
  });
});

describe("AccentPicker — curated palette", () => {
  it("offers only the curated colours plus a neutral no-accent option, no free-form input", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    const grid = await screen.findByTestId("accent-picker-swatches");
    expect(within(grid).getAllByRole("radio")).toHaveLength(ACCENT_COLORS.length + 1);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("shows no colour selected and a disabled reset when the repository has no explicit choice", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    for (const radio of screen.getAllByRole("radio")) {
      if (radio.getAttribute("data-testid")?.startsWith("accent-tile-")) {
        expect(radio).toHaveAttribute("aria-checked", "false");
      }
    }
    expect(screen.getByTestId("accent-picker-reset")).toBeDisabled();
    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
  });

  it("marks the repository's own explicit colour as selected", async () => {
    mockEngineList([baseEngine({ color: "ruby", intensity: "standard" })]);
    mockEngine({ color: "ruby", intensity: "standard" });
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    expect(await screen.findByRole("radio", { name: "Ruby" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByTestId("accent-picker-reset")).toBeEnabled();
  });

  it("loads an already-stored explicit no-accent choice as selected", async () => {
    mockEngineList([baseEngine({ color: null, intensity: "standard" })]);
    mockEngine({ color: null, intensity: "standard" });
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    expect(await screen.findByRole("radio", { name: "No accent" })).toHaveAttribute("aria-checked", "true");
  });

  it("marks a colour already claimed by another connected repository", async () => {
    mockEngineList([
      baseEngine(),
      makeEngineSummary({
        repo: { owner: "other", repo: "repo" },
        posture: "propose",
        accent: { color: "plum", intensity: "standard" },
      }),
    ]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    expect(
      await screen.findByRole("radio", { name: "Plum — already used by another repository" }),
    ).toBeInTheDocument();
  });
});

describe("AccentPicker — selecting, saving, and reverting", () => {
  it("marks the picker dirty after selecting a colour and clears it after a successful save", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    mockSaveAccent();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    expect(screen.getByTestId("accent-picker-unsaved")).toBeInTheDocument();
    expect(screen.getByTestId("accent-picker-save")).toBeEnabled();

    await user.click(screen.getByTestId("accent-picker-save"));
    await waitFor(() => {
      expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("accent-picker-save")).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Iris" })).toHaveAttribute("aria-checked", "true");
  });

  it("reverts to the last accepted value and shows an error when the save fails", async () => {
    mockEngineList([baseEngine({ color: "ruby", intensity: "standard" })]);
    mockEngine({ color: "ruby", intensity: "standard" });
    mockSaveAccent(500);
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    await user.click(screen.getByTestId("accent-picker-save"));

    expect(await screen.findByTestId("accent-picker-save-error")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Ruby" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Iris" })).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
    expect(screen.getByTestId("accent-picker-save")).toBeDisabled();
  });

  it("selecting the neutral no-accent swatch and saving persists an explicit no-accent choice", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    mockSaveAccent();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "No accent" }));
    await user.click(screen.getByTestId("accent-picker-save"));
    await waitFor(() => {
      expect(screen.getByRole("radio", { name: "No accent" })).toHaveAttribute("aria-checked", "true");
    });
    expect(screen.getByTestId("accent-picker-save")).toBeDisabled();
  });

  it("resets to automatic, clearing an explicit selection back to the picker's original state", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    expect(screen.getByTestId("accent-picker-unsaved")).toBeInTheDocument();

    await user.click(screen.getByTestId("accent-picker-reset"));
    expect(screen.getByRole("radio", { name: "Iris" })).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
    expect(screen.getByTestId("accent-picker-reset")).toBeDisabled();
  });

  it("saves null to revert an explicit choice back to automatic", async () => {
    mockEngineList([baseEngine({ color: "ruby", intensity: "standard" })]);
    mockEngine({ color: "ruby", intensity: "standard" });
    let capturedBody: unknown;
    server.use(
      http.put(`/api/engines/${OWNER}/${REPO}/accent`, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ repo: { owner: OWNER, repo: REPO }, posture: "propose", accent: null });
      }),
    );
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByTestId("accent-picker-reset"));
    await user.click(screen.getByTestId("accent-picker-save"));
    await waitFor(() => {
      expect(capturedBody).toEqual({ accent: null });
    });
  });
});

describe("AccentPicker — intensity and live preview", () => {
  it("disables the intensity control until a colour is chosen", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    expect(screen.getByTestId("accent-picker-intensity-soft")).toBeDisabled();
  });

  it("enables the intensity control once a colour is chosen and updates the preview edge", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    const boldButton = screen.getByTestId("accent-picker-intensity-bold");
    expect(boldButton).toBeEnabled();
    await user.click(boldButton);
    expect(boldButton).toHaveAttribute("aria-checked", "true");

    const preview = screen.getByTestId("accent-picker-preview");
    expect(preview.style.getPropertyValue("--accent-edge")).toBe("#9165f5");
  });

  it("disables intensity again after selecting the neutral no-accent swatch", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    await user.click(screen.getByRole("radio", { name: "No accent" }));
    expect(screen.getByTestId("accent-picker-intensity-soft")).toBeDisabled();
  });

  it("previews the neutral border token, not an accent edge, when no-accent is chosen", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "No accent" }));
    const preview = screen.getByTestId("accent-picker-preview");
    expect(preview.style.getPropertyValue("--accent-edge")).toBe("");
    expect(preview.className).toContain("border-border");
  });

  it("previews the automatically resolved colour before any explicit choice is made", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    const preview = screen.getByTestId("accent-picker-preview");
    expect(preview.style.getPropertyValue("--accent-edge")).toMatch(/^#[0-9a-f]{6}$/i);
  });
});

describe("AccentPicker — intensity/no-accent dirty-state equality", () => {
  it("stays clean when returning to the committed explicit no-accent choice after changing intensity", async () => {
    mockEngineList([baseEngine({ color: null, intensity: "standard" })]);
    mockEngine({ color: null, intensity: "standard" });
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    await user.click(screen.getByRole("radio", { name: "Iris" }));
    await user.click(screen.getByTestId("accent-picker-intensity-bold"));
    await user.click(screen.getByRole("radio", { name: "No accent" }));

    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
    expect(screen.getByTestId("accent-picker-save")).toBeDisabled();
  });
});

describe("AccentPicker — keyboard navigation (radiogroup)", () => {
  it("moves focus and selection forward through swatches with ArrowRight, wrapping at the end", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    const noAccent = screen.getByRole("radio", { name: "No accent" });
    noAccent.focus();
    fireEvent.keyDown(noAccent, { key: "ArrowRight" });

    const iris = screen.getByRole("radio", { name: "Iris" });
    expect(iris).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(iris);
  });

  it("moves focus and selection backward through swatches with ArrowLeft, wrapping to the last colour", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    const noAccent = screen.getByRole("radio", { name: "No accent" });
    noAccent.focus();
    fireEvent.keyDown(noAccent, { key: "ArrowLeft" });

    const bone = screen.getByRole("radio", { name: "Bone" });
    expect(bone).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(bone);
  });

  it("traverses every tile in order via repeated ArrowRight, each with a present and unique accessible name", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    const tileCount = ACCENT_COLORS.length + 1;
    let current = screen.getByRole("radio", { name: "No accent" });
    current.focus();
    const seen = [current.getAttribute("aria-label")];
    for (let i = 1; i < tileCount; i += 1) {
      fireEvent.keyDown(current, { key: "ArrowRight" });
      current = document.activeElement as HTMLElement;
      seen.push(current.getAttribute("aria-label"));
    }
    expect(seen.every((label) => label !== null && label.length > 0)).toBe(true);
    expect(new Set(seen).size).toBe(tileCount);

    fireEvent.keyDown(current, { key: "ArrowRight" });
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: "No accent" }));
  });

  it("ignores non-arrow keys on the swatch grid", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");

    const noAccent = screen.getByRole("radio", { name: "No accent" });
    noAccent.focus();
    fireEvent.keyDown(noAccent, { key: "a" });

    expect(noAccent).toHaveAttribute("aria-checked", "false");
    expect(screen.queryByTestId("accent-picker-unsaved")).not.toBeInTheDocument();
  });

  it("moves intensity forward with ArrowRight and wraps at the end", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    await user.click(screen.getByRole("radio", { name: "Iris" }));

    const standard = screen.getByTestId("accent-picker-intensity-standard");
    standard.focus();
    fireEvent.keyDown(standard, { key: "ArrowRight" });
    const bold = screen.getByTestId("accent-picker-intensity-bold");
    expect(bold).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(bold);

    fireEvent.keyDown(bold, { key: "ArrowRight" });
    const soft = screen.getByTestId("accent-picker-intensity-soft");
    expect(soft).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(soft);
  });

  it("moves intensity backward with ArrowLeft", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    await user.click(screen.getByRole("radio", { name: "Iris" }));

    const standard = screen.getByTestId("accent-picker-intensity-standard");
    standard.focus();
    fireEvent.keyDown(standard, { key: "ArrowLeft" });
    const soft = screen.getByTestId("accent-picker-intensity-soft");
    expect(soft).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(soft);
  });

  it("ignores non-arrow keys on the intensity control", async () => {
    mockEngineList([baseEngine()]);
    mockEngine();
    const user = userEvent.setup();
    render(<AccentPicker engineKey={ENGINE_KEY} />);
    await screen.findByTestId("accent-picker-swatches");
    await user.click(screen.getByRole("radio", { name: "Iris" }));

    const standard = screen.getByTestId("accent-picker-intensity-standard");
    standard.focus();
    fireEvent.keyDown(standard, { key: "a" });
    expect(standard).toHaveAttribute("aria-checked", "true");
  });
});
