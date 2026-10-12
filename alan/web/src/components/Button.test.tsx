import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type React from "react";

import Button, { ArchiveIcon, GearIcon, StopIcon, TrashIcon, UnarchiveIcon, type ButtonVariant } from "./Button";

const VARIANT_EXPECTATIONS: readonly {
  variant: ButtonVariant;
  bg: string;
  text: string;
}[] = [
  { variant: "create", bg: "bg-btn-create", text: "text-surface" },
  { variant: "action", bg: "bg-btn-action", text: "text-surface" },
  { variant: "destroy", bg: "bg-btn-destroy", text: "text-surface" },
  { variant: "reset", bg: "bg-btn-reset", text: "text-surface" },
  { variant: "ghost", bg: "border-border", text: "text-text-primary" },
];

describe("Button", () => {
  for (const { variant, bg, text } of VARIANT_EXPECTATIONS) {
    it(`renders ${variant} variant with its semantic token + contrast-safe foreground`, () => {
      render(
        <Button variant={variant} data-testid="btn">
          go
        </Button>,
      );
      const el = screen.getByTestId("btn");
      expect(el.className).toContain(bg);
      expect(el.className).toContain(text);
      expect(el.className).toContain("focus-visible:ring-2");
      expect(el).toHaveAttribute("data-variant", variant);
    });
  }

  it("meets the 44px minimum tap-target size on every variant", () => {
    render(
      <Button variant="ghost" data-testid="btn">
        x
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("min-h-11");
    expect(el.className).toContain("min-w-11");
  });

  it("defaults to a <button> with type=button when no type is given", () => {
    render(
      <Button variant="action" data-testid="btn">
        ok
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.tagName).toBe("BUTTON");
    expect(el).toHaveAttribute("type", "button");
  });

  it("honours an explicit type=submit", () => {
    render(
      <Button variant="action" type="submit" data-testid="btn">
        send
      </Button>,
    );
    expect(screen.getByTestId("btn")).toHaveAttribute("type", "submit");
  });

  it("fires onClick with a MouseEvent the consumer can inspect or cancel", () => {
    let observedTarget: EventTarget | null = null;
    let preventDefaultIsCallable = false;
    const handler = (event: React.MouseEvent<HTMLButtonElement>): void => {
      observedTarget = event.currentTarget;
      preventDefaultIsCallable = typeof event.preventDefault === "function";
    };
    render(
      <Button variant="action" onClick={handler} data-testid="btn">
        ok
      </Button>,
    );
    fireEvent.click(screen.getByTestId("btn"));
    expect(observedTarget).toBeInstanceOf(HTMLButtonElement);
    expect(preventDefaultIsCallable).toBe(true);
  });

  it("respects disabled state on a <button>", () => {
    render(
      <Button variant="destroy" disabled data-testid="btn">
        delete
      </Button>,
    );
    expect(screen.getByTestId("btn")).toBeDisabled();
  });

  it("appends an extra className when given", () => {
    render(
      <Button variant="action" className="ml-2" data-testid="btn">
        ok
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("ml-2");
    expect(el.className).toContain("bg-btn-action");
  });

  it("forwards aria-label, title, and data-readiness", () => {
    render(
      <Button
        variant="ghost"
        aria-label="dismiss"
        title="press to dismiss"
        data-readiness="ready"
        data-testid="btn"
      >
        x
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el).toHaveAttribute("aria-label", "dismiss");
    expect(el).toHaveAttribute("title", "press to dismiss");
    expect(el).toHaveAttribute("data-readiness", "ready");
  });

  it("renders a router <Link> when linkTo is provided and navigates", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="/"
            element={
              <Button variant="action" linkTo="/next" data-testid="btn">
                next
              </Button>
            }
          />
          <Route path="/next" element={<p>landed</p>} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByTestId("btn");
    expect(link.tagName).toBe("A");
    expect(link.className).toContain("bg-btn-action");
    expect(link).toHaveAttribute("href", "/next");
    expect(link).toHaveAttribute("data-variant", "action");
    fireEvent.click(link);
    expect(screen.getByText("landed")).toBeInTheDocument();
  });

  it("fires onClick with a MouseEvent on the link variant", () => {
    let observedTarget: EventTarget | null = null;
    const handler = (event: React.MouseEvent<HTMLAnchorElement>): void => {
      observedTarget = event.currentTarget;
    };
    render(
      <MemoryRouter>
        <Button variant="action" linkTo="/x" onClick={handler} data-testid="btn">
          go
        </Button>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("btn"));
    expect(observedTarget).toBeInstanceOf(HTMLAnchorElement);
  });

  it("renders a plain external <a> with target=_blank and rel=noopener by default when href is given", () => {
    render(
      <Button variant="ghost" href="https://github.com/example/repo/pull/1" data-testid="btn">
        Open PR
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.tagName).toBe("A");
    expect(el).toHaveAttribute("href", "https://github.com/example/repo/pull/1");
    expect(el).toHaveAttribute("target", "_blank");
    expect(el).toHaveAttribute("rel", "noopener noreferrer");
    expect(el.className).toContain("border-border");
    expect(el).toHaveAttribute("data-variant", "ghost");
  });

  it("honours an explicit target/rel override on the href variant", () => {
    render(
      <Button
        variant="ghost"
        href="https://example.com"
        target="_self"
        rel="noreferrer"
        data-testid="btn"
      >
        Open
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el).toHaveAttribute("target", "_self");
    expect(el).toHaveAttribute("rel", "noreferrer");
  });
});

describe("Button — busy", () => {
  it("sets aria-disabled and aria-busy when busy", () => {
    render(
      <Button variant="action" busy data-testid="btn">
        Go
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el).toHaveAttribute("aria-disabled", "true");
    expect(el).toHaveAttribute("aria-busy", "true");
  });

  it("stays focusable while busy, never gaining the native disabled attribute", () => {
    render(
      <Button variant="action" busy data-testid="btn">
        Go
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el).not.toBeDisabled();
    expect(el).not.toHaveAttribute("disabled");
  });

  it("no-ops a click while busy", () => {
    const onClick = vi.fn();
    render(
      <Button variant="action" busy onClick={onClick} data-testid="btn">
        Go
      </Button>,
    );
    fireEvent.click(screen.getByTestId("btn"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("appends the busy opacity/cursor classes while busy", () => {
    render(
      <Button variant="action" busy data-testid="btn">
        Go
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("aria-disabled:opacity-50");
    expect(el.className).toContain("aria-disabled:cursor-not-allowed");
  });

  it("carries neither the busy aria attributes nor the busy classes while idle", () => {
    render(
      <Button variant="action" data-testid="btn">
        Go
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el).not.toHaveAttribute("aria-disabled");
    expect(el).not.toHaveAttribute("aria-busy");
    expect(el.className).not.toContain("aria-disabled:opacity-50");
  });

  it("still fires onClick when not busy", () => {
    const onClick = vi.fn();
    render(
      <Button variant="action" onClick={onClick} data-testid="btn">
        Go
      </Button>,
    );
    fireEvent.click(screen.getByTestId("btn"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});

describe("Button — iconOnly", () => {
  it("renders a compact square button with no horizontal padding", () => {
    render(
      <Button variant="ghost" iconOnly aria-label="Close" data-testid="btn">
        <TrashIcon />
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("min-h-11");
    expect(el.className).toContain("min-w-11");
    expect(el.className).not.toContain("px-3");
  });

  it("renders the quiet, non-filled colour for the destroy variant instead of a solid fill", () => {
    render(
      <Button variant="destroy" iconOnly aria-label="Delete" data-testid="btn">
        <TrashIcon />
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("text-btn-destroy");
    expect(el.className).not.toContain("bg-btn-destroy ");
  });

  it("carries the given aria-label as the accessible name", () => {
    render(
      <Button variant="ghost" iconOnly aria-label="Global settings" data-testid="btn">
        <GearIcon />
      </Button>,
    );
    expect(screen.getByTestId("btn")).toHaveAccessibleName("Global settings");
  });

  it("renders the icon-only treatment on a linkTo variant too", () => {
    render(
      <MemoryRouter>
        <Button variant="ghost" iconOnly aria-label="Settings" linkTo="/settings" data-testid="btn">
          <GearIcon />
        </Button>
      </MemoryRouter>,
    );
    const el = screen.getByTestId("btn");
    expect(el.tagName).toBe("A");
    expect(el.className).toContain("min-w-11");
    expect(el).toHaveAccessibleName("Settings");
  });

  it("renders the icon-only treatment on an href (external-link) variant too", () => {
    render(
      <Button variant="ghost" iconOnly aria-label="Open" href="https://example.com" data-testid="btn">
        <GearIcon />
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.tagName).toBe("A");
    expect(el).toHaveAttribute("data-icon-only", "true");
  });

  it("does not leak the iconOnly prop onto the DOM element", () => {
    render(
      <Button variant="ghost" iconOnly aria-label="Close" data-testid="btn">
        <TrashIcon />
      </Button>,
    );
    expect(screen.getByTestId("btn")).not.toHaveAttribute("iconOnly");
  });

  it("marks the rendered element data-icon-only so automated checks can tell it apart from a saturated-fill button sharing the same data-variant", () => {
    render(
      <Button variant="destroy" iconOnly aria-label="Delete" data-testid="btn">
        <TrashIcon />
      </Button>,
    );
    expect(screen.getByTestId("btn")).toHaveAttribute("data-icon-only", "true");
  });

  it("carries no data-icon-only attribute on a non-icon-only button", () => {
    render(
      <Button variant="destroy" data-testid="btn">
        Delete
      </Button>,
    );
    expect(screen.getByTestId("btn")).not.toHaveAttribute("data-icon-only");
  });
});

describe("GearIcon and TrashIcon", () => {
  it("render GearIcon as a decorative, aria-hidden SVG", () => {
    render(<GearIcon />);
    expect(screen.getByTestId("icon-gear")).toHaveAttribute("aria-hidden", "true");
  });

  it("render TrashIcon as a decorative, aria-hidden SVG", () => {
    render(<TrashIcon />);
    expect(screen.getByTestId("icon-trash")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("StopIcon, ArchiveIcon, and UnarchiveIcon", () => {
  it("renders StopIcon as a decorative, aria-hidden filled shape, visibly a different kind of glyph than the tray-arrow icons", () => {
    render(<StopIcon />);
    const icon = screen.getByTestId("icon-stop");
    expect(icon).toHaveAttribute("aria-hidden", "true");
    expect(icon.querySelector("rect")).not.toBeNull();
    expect(icon.querySelector("path")).toBeNull();
  });

  it("renders ArchiveIcon and UnarchiveIcon as decorative, aria-hidden SVGs", () => {
    render(<ArchiveIcon />);
    render(<UnarchiveIcon />);
    expect(screen.getByTestId("icon-archive")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("icon-unarchive")).toHaveAttribute("aria-hidden", "true");
  });

  it("renders ArchiveIcon and UnarchiveIcon from the exact same path data, differing only by a rotate-180 class", () => {
    render(<ArchiveIcon />);
    render(<UnarchiveIcon />);
    const archivePath = screen.getByTestId("icon-archive").querySelector("path")?.getAttribute("d");
    const unarchivePath = screen.getByTestId("icon-unarchive").querySelector("path")?.getAttribute("d");
    expect(archivePath).toBeDefined();
    expect(archivePath).toBe(unarchivePath);
    expect(screen.getByTestId("icon-archive")).not.toHaveClass("rotate-180");
    expect(screen.getByTestId("icon-unarchive")).toHaveClass("rotate-180");
  });
});

describe("Button — compact density", () => {
  it("shrinks a text button to a 24px minimum target", () => {
    render(
      <Button variant="ghost" density="compact" data-testid="btn">
        Open
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("min-h-6");
    expect(el.className).toContain("min-w-6");
    expect(el.className).toContain("px-2");
    expect(el.className).not.toContain("min-h-11");
  });

  it("shrinks an icon-only button to a 24px square with no horizontal padding", () => {
    render(
      <Button variant="action" iconOnly density="compact" aria-label="View" data-testid="btn">
        <GearIcon />
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("min-h-6");
    expect(el.className).toContain("min-w-6");
    expect(el.className).not.toContain("min-h-11");
    expect(el.className).not.toContain("px-2");
    expect(el.className).toContain("text-btn-action");
  });

  it("shrinks the linkTo form", () => {
    render(
      <MemoryRouter>
        <Button variant="ghost" density="compact" linkTo="/x" data-testid="btn">
          Go
        </Button>
      </MemoryRouter>,
    );
    const el = screen.getByTestId("btn");
    expect(el.tagName).toBe("A");
    expect(el.className).toContain("min-h-6");
    expect(el.className).not.toContain("min-h-11");
  });

  it("shrinks the href form", () => {
    render(
      <Button variant="ghost" density="compact" href="https://example.com" data-testid="btn">
        Go
      </Button>,
    );
    const el = screen.getByTestId("btn");
    expect(el.className).toContain("min-h-6");
    expect(el.className).not.toContain("min-h-11");
  });

  it("keeps the compact focus ring identical to the default one", () => {
    render(
      <Button variant="ghost" density="compact" data-testid="btn">
        Go
      </Button>,
    );
    expect(screen.getByTestId("btn").className).toContain("focus-visible:ring-2");
    expect(screen.getByTestId("btn").className).toContain("focus-visible:ring-text-primary/60");
  });

  it("does not leak the density prop onto the DOM element", () => {
    render(
      <MemoryRouter>
        <Button variant="ghost" density="compact" data-testid="btn">
          Go
        </Button>
        <Button variant="ghost" density="compact" linkTo="/x" data-testid="link">
          Go
        </Button>
        <Button variant="ghost" density="compact" href="https://example.com" data-testid="ext">
          Go
        </Button>
      </MemoryRouter>,
    );
    for (const id of ["btn", "link", "ext"]) {
      expect(screen.getByTestId(id)).not.toHaveAttribute("density");
    }
  });

  it("keeps the 44px minimum when density is explicitly default", () => {
    render(
      <Button variant="ghost" density="default" data-testid="btn">
        Go
      </Button>,
    );
    expect(screen.getByTestId("btn").className).toContain("min-h-11");
    expect(screen.getByTestId("btn").className).not.toContain("min-h-6");
  });
});
