import { screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { useEngines } from "../hooks/useEngines";
import {
  DEFAULT_KEY_STATES,
  engineListingHandler,
  LISTED_DEFAULT_KEY,
  makeEngineSummaryFor,
  settleDefaultEngineKey,
  STUB_DEFAULT_ENGINE_KEY,
} from "../test/engineFixtures";
import { renderWithQuery } from "../test/renderWithQuery";
import { server } from "../test/server";
import EngineBadge from "./EngineBadge";

function ListingProbe() {
  const { loaded } = useEngines();
  return <span data-testid="listing-loaded">{String(loaded)}</span>;
}

function stubListing(defaultKey: string | undefined): void {
  server.use(
    engineListingHandler({
      engines: [
        makeEngineSummaryFor(STUB_DEFAULT_ENGINE_KEY),
        makeEngineSummaryFor(LISTED_DEFAULT_KEY),
      ],
      defaultKey,
    }),
  );
}

async function renderAfterListing(badge: ReactElement): Promise<void> {
  renderWithQuery(
    <>
      <ListingProbe />
      {badge}
    </>,
  );
  await waitFor(() => {
    expect(screen.getByTestId("listing-loaded")).toHaveTextContent("true");
  });
}

describe("EngineBadge", () => {
  it("renders nothing when engineKey is null", () => {
    const { container } = renderWithQuery(<EngineBadge engineKey={null} testIdSuffix="x" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the slug for a non-default engine", () => {
    settleDefaultEngineKey(null);
    renderWithQuery(<EngineBadge engineKey="sprocket-co/sprocket" testIdSuffix="f1" />);
    const badge = screen.getByTestId("engine-badge-f1");
    expect(badge).toHaveTextContent("sprocket-co/sprocket");
  });

  it("uses the visible text as the accessible name", () => {
    settleDefaultEngineKey(null);
    renderWithQuery(<EngineBadge engineKey="sprocket-co/sprocket" testIdSuffix="f2" />);
    expect(screen.getByText("sprocket-co/sprocket")).toBeInTheDocument();
  });

  it("renders nothing for the built-in key before the listing loads and showDefault is omitted", () => {
    const { container } = renderWithQuery(
      <EngineBadge engineKey={STUB_DEFAULT_ENGINE_KEY} testIdSuffix="pre" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing for any key before the listing loads, since the default is not yet known", () => {
    const { container } = renderWithQuery(
      <EngineBadge engineKey="sprocket-co/sprocket" testIdSuffix="unsettled" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("still renders the key before the listing loads when showDefault is true", () => {
    renderWithQuery(<EngineBadge engineKey="sprocket-co/sprocket" testIdSuffix="shown-early" showDefault />);
    expect(screen.getByTestId("engine-badge-shown-early")).toHaveTextContent("sprocket-co/sprocket");
  });
});

describe("EngineBadge — listed default repository", () => {
  it.each(DEFAULT_KEY_STATES)(
    "shows the built-in key's badge only when the listed default differs from it (%s)",
    async (state, defaultKey) => {
      stubListing(defaultKey);
      await renderAfterListing(
        <EngineBadge engineKey={STUB_DEFAULT_ENGINE_KEY} testIdSuffix="built-in" />,
      );
      const badge = screen.queryByTestId("engine-badge-built-in");
      if (state === "other") {
        expect(badge).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
      } else {
        expect(badge).not.toBeInTheDocument();
      }
    },
  );

  it("hides the listed default repository's badge once the listing loads", async () => {
    stubListing(LISTED_DEFAULT_KEY);
    await renderAfterListing(<EngineBadge engineKey={LISTED_DEFAULT_KEY} testIdSuffix="listed" />);
    expect(screen.queryByTestId("engine-badge-listed")).not.toBeInTheDocument();
  });

  it("renders nothing for the listed default repository before the listing loads", () => {
    const { container } = renderWithQuery(
      <EngineBadge engineKey={LISTED_DEFAULT_KEY} testIdSuffix="listed-pre" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows every keyed badge once the listing names no default", async () => {
    stubListing(undefined);
    await renderAfterListing(
      <EngineBadge engineKey={STUB_DEFAULT_ENGINE_KEY} testIdSuffix="no-default" />,
    );
    expect(screen.getByTestId("engine-badge-no-default")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
  });

  it.each(DEFAULT_KEY_STATES)(
    "still renders the built-in key when showDefault is explicitly true (%s)",
    async (_state, defaultKey) => {
      stubListing(defaultKey);
      await renderAfterListing(
        <EngineBadge engineKey={STUB_DEFAULT_ENGINE_KEY} testIdSuffix="shown" showDefault />,
      );
      expect(screen.getByTestId("engine-badge-shown")).toHaveTextContent(STUB_DEFAULT_ENGINE_KEY);
    },
  );

  it("still renders the listed default repository when showDefault is explicitly true", async () => {
    stubListing(LISTED_DEFAULT_KEY);
    await renderAfterListing(
      <EngineBadge engineKey={LISTED_DEFAULT_KEY} testIdSuffix="listed-shown" showDefault />,
    );
    expect(screen.getByTestId("engine-badge-listed-shown")).toHaveTextContent(LISTED_DEFAULT_KEY);
  });

  it.each(DEFAULT_KEY_STATES)(
    "renders nothing for a null engine key even when showDefault is true (%s)",
    async (_state, defaultKey) => {
      stubListing(defaultKey);
      await renderAfterListing(<EngineBadge engineKey={null} testIdSuffix="none" showDefault />);
      expect(screen.queryByTestId("engine-badge-none")).not.toBeInTheDocument();
    },
  );
});
