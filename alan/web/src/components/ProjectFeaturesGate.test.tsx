import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it } from "vitest";

import { writeProjectFeaturesOn } from "../projectFeatures";
import ProjectFeaturesGate from "./ProjectFeaturesGate";

function renderGated(initial = "/projects") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Routes>
        <Route
          path="/projects"
          element={
            <ProjectFeaturesGate>
              <div data-testid="gated-content">gated</div>
            </ProjectFeaturesGate>
          }
        />
        <Route path="/" element={<div data-testid="home-page">home</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProjectFeaturesGate", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders its children when the preference is on", () => {
    writeProjectFeaturesOn(true);
    renderGated();
    expect(screen.getByTestId("gated-content")).toBeInTheDocument();
  });

  it("redirects to home instead of rendering its children when the preference is off", () => {
    renderGated();
    expect(screen.queryByTestId("gated-content")).not.toBeInTheDocument();
    expect(screen.getByTestId("home-page")).toBeInTheDocument();
  });
});
