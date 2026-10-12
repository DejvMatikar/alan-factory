import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import ConnectBlankSlate from "./ConnectBlankSlate";

const FILLED_VARIANT_SELECTOR =
  '[data-variant="create"], [data-variant="action"], [data-variant="destroy"], [data-variant="reset"]';

function renderBlankSlate() {
  return render(
    <MemoryRouter>
      <ConnectBlankSlate />
    </MemoryRouter>,
  );
}

describe("ConnectBlankSlate", () => {
  it("renders the four what-connecting-does lines", () => {
    renderBlankSlate();
    expect(
      screen.getByText(/reads the repo and proposes an engine config/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/opens one scaffold pr that you review and merge/i)).toBeInTheDocument();
    expect(
      screen.getByText(/applies branch protection and names the authoritative checks/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/you choose a posture/i)).toBeInTheDocument();
  });

  it("points at the top bar's Connect repo action rather than duplicating it", () => {
    renderBlankSlate();
    expect(
      screen.getByText(/Connect repo, in the top bar, starts this/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/one PR to review · nothing dispatches until you say so/),
    ).toBeInTheDocument();
  });

  it("renders no filled CTA of its own — the top bar carries the one connect action", () => {
    renderBlankSlate();
    const filled = document.querySelectorAll(FILLED_VARIANT_SELECTOR);
    expect(filled).toHaveLength(0);
    expect(screen.queryByTestId("connect-repo-cta")).not.toBeInTheDocument();
  });

  it("never claims CI provenance anywhere in its copy", () => {
    renderBlankSlate();
    expect(document.body.textContent).not.toMatch(/\bCI\b/);
  });
});
