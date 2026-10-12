import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { SettingsUiContext } from "../../../shell/SettingsUiProvider";
import NeedsYouLines, { type NeedsYouLinesProps } from "./NeedsYouLines";

function renderLines(props: NeedsYouLinesProps, open = vi.fn()) {
  const rendered = render(
    <MemoryRouter>
      <SettingsUiContext.Provider value={{ isOpen: false, open, close: vi.fn(), closedCount: 0 }}>
        <NeedsYouLines {...props} />
      </SettingsUiContext.Provider>
    </MemoryRouter>,
  );
  return { ...rendered, open };
}

describe("NeedsYouLines", () => {
  it("renders nothing when both inputs are empty", () => {
    const { container } = renderLines({ hostSetupMessage: null, resumes: [] });
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the host-setup line and invokes the settings opener on click", async () => {
    const { open } = renderLines({
      hostSetupMessage: "Finish host setup — control token not set",
      resumes: [],
    });
    const user = userEvent.setup();
    const line = screen.getByTestId("needs-you-host-setup");
    expect(line).toHaveTextContent("Finish host setup — control token not set");
    await user.click(line);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("renders no resume lines when hostSetupMessage is set but resumes is empty", () => {
    renderLines({ hostSetupMessage: "Finish host setup — control token not set", resumes: [] });
    expect(screen.queryByTestId(/needs-you-resume-/)).not.toBeInTheDocument();
  });

  it("renders one resume line per mid-setup repo with the bare deep-link and step label", () => {
    renderLines({
      hostSetupMessage: null,
      resumes: [
        { owner: "acme", repo: "widget", step: 1 },
        { owner: "globex", repo: "gizmo", step: 3 },
      ],
    });
    const first = screen.getByTestId("needs-you-resume-acme-widget");
    expect(first).toHaveTextContent("Finish setup of acme/widget — step 1 →");
    expect(first).toHaveAttribute("href", "/connect/acme/widget");
    const second = screen.getByTestId("needs-you-resume-globex-gizmo");
    expect(second).toHaveTextContent("Finish setup of globex/gizmo — step 3 →");
    expect(second).toHaveAttribute("href", "/connect/globex/gizmo");
  });

  it("renders both a host line and resume lines together", () => {
    renderLines({
      hostSetupMessage: "Finish host setup — forge credential not set",
      resumes: [{ owner: "acme", repo: "widget", step: 2 }],
    });
    expect(screen.getByTestId("needs-you-host-setup")).toBeInTheDocument();
    expect(screen.getByTestId("needs-you-resume-acme-widget")).toBeInTheDocument();
  });
});
