import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { REVEAL_Z_CLASS } from "../shell/stickyOffsets";
import HoverReveal from "./HoverReveal";

function renderReveal() {
  return render(
    <HoverReveal id="desc-1" description="Consequence sentence.">
      <button type="button">Trigger</button>
    </HoverReveal>,
  );
}

describe("HoverReveal", () => {
  it("wires the description onto the child automatically via aria-describedby", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    expect(description).toHaveAttribute("id", "desc-1");
    expect(description).toHaveClass("sr-only");
    expect(screen.getByRole("button")).toHaveAttribute("aria-describedby", "desc-1");
  });

  it("overrides a child's own aria-describedby with its own id, since it owns the association", () => {
    render(
      <HoverReveal id="desc-owned" description="Consequence sentence.">
        <button type="button" aria-describedby="something-unrelated">
          Trigger
        </button>
      </HoverReveal>,
    );
    expect(screen.getByRole("button")).toHaveAttribute("aria-describedby", "desc-owned");
  });

  it("leaves a non-element child untouched", () => {
    render(<HoverReveal id="desc-text" description="Consequence sentence.">Just text</HoverReveal>);
    expect(screen.getByText("Just text")).toBeInTheDocument();
  });

  it("reveals the description on pointer hover", async () => {
    const user = userEvent.setup();
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    await user.hover(screen.getByRole("button"));
    expect(description).not.toHaveClass("sr-only");
  });

  it("hides the description again on pointer leave", async () => {
    const user = userEvent.setup();
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    await user.hover(screen.getByRole("button"));
    await user.unhover(screen.getByRole("button"));
    expect(description).toHaveClass("sr-only");
  });

  it("reveals the description on keyboard focus", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(description).not.toHaveClass("sr-only");
  });

  it("hides the description again on blur", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    const button = screen.getByRole("button");
    act(() => {
      button.focus();
    });
    act(() => {
      button.blur();
    });
    expect(description).toHaveClass("sr-only");
  });

  it("keeps the description revealed on pointer leave while focus still holds it open", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    const button = screen.getByRole("button");
    act(() => {
      button.focus();
    });
    fireEvent.pointerEnter(button);
    fireEvent.pointerLeave(button);
    expect(description).not.toHaveClass("sr-only");
  });

  it("keeps the description revealed on blur while the pointer still holds it open", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    const button = screen.getByRole("button");
    fireEvent.pointerEnter(button);
    act(() => {
      button.focus();
    });
    act(() => {
      button.blur();
    });
    expect(description).not.toHaveClass("sr-only");
  });

  it("dismisses on Escape while open", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(description).not.toHaveClass("sr-only");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(description).toHaveClass("sr-only");
  });

  it("dismisses on Escape while only hovered, using the realistic document.body target of an unfocused keypress", async () => {
    const user = userEvent.setup();
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    await user.hover(screen.getByRole("button"));
    expect(description).not.toHaveClass("sr-only");
    fireEvent.keyDown(document.body, { key: "Escape" });
    expect(description).toHaveClass("sr-only");
  });

  it("ignores a non-Escape key while open", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    fireEvent.keyDown(document, { key: "Enter" });
    expect(description).not.toHaveClass("sr-only");
  });

  it("attaches the capture-phase Escape listener only while open, and removes it on close", () => {
    const addSpy = vi.spyOn(document, "addEventListener");
    const removeSpy = vi.spyOn(document, "removeEventListener");
    renderReveal();
    expect(addSpy).not.toHaveBeenCalledWith("keydown", expect.any(Function), true);

    act(() => {
      screen.getByRole("button").focus();
    });
    expect(addSpy).toHaveBeenCalledWith("keydown", expect.any(Function), true);
    const addCallCount = addSpy.mock.calls.filter(([type]) => type === "keydown").length;

    act(() => {
      screen.getByRole("button").blur();
    });
    const removeCallCount = removeSpy.mock.calls.filter(([type]) => type === "keydown").length;
    expect(removeCallCount).toBe(addCallCount);

    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("prevents Escape's default action so a reveal nested in a native dialog doesn't also fire the dialog's own cancel event", () => {
    renderReveal();
    act(() => {
      screen.getByRole("button").focus();
    });
    const event = fireEvent.keyDown(document, { key: "Escape", cancelable: true });
    expect(event).toBe(false);
  });

  it("defaults to the left edge without an align prop", () => {
    renderReveal();
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(screen.getByText("Consequence sentence.")).toHaveClass("left-0");
  });

  it("stacks the revealed description on the shared reveal layer, above sticky page chrome", () => {
    renderReveal();
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(screen.getByText("Consequence sentence.").className).toContain(REVEAL_Z_CLASS);
  });

  it("positions the description at the right edge when align is 'end'", () => {
    render(
      <HoverReveal id="desc-align" description="Right-edge sentence." align="end">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(screen.getByText("Right-edge sentence.")).toHaveClass("right-0");
  });

  it("renders no role attribute on the wrapper by default", () => {
    renderReveal();
    expect(screen.getByRole("button").parentElement).not.toHaveAttribute("role");
  });

  it("marks the wrapper span presentation-role when wrapperRole is given, keeping it ARIA-transparent", () => {
    render(
      <HoverReveal id="desc-presentation" description="Presentation sentence." wrapperRole="presentation">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    expect(screen.getByRole("button").parentElement).toHaveAttribute("role", "presentation");
  });

  it("does not swallow an Escape meant for an unrelated dialog opened by its own trigger, even while the trigger still holds focus", () => {
    render(
      <>
        <HoverReveal id="desc-unrelated" description="Trigger sentence.">
          <button type="button">Trigger</button>
        </HoverReveal>
        <dialog open aria-label="Unrelated dialog" data-testid="unrelated-dialog" />
      </>,
    );
    const trigger = screen.getByRole("button");
    act(() => {
      trigger.focus();
    });
    expect(screen.getByText("Trigger sentence.")).not.toHaveClass("sr-only");
    const notPrevented = fireEvent.keyDown(trigger, {
      key: "Escape",
      cancelable: true,
    });
    expect(notPrevented).toBe(true);
    expect(screen.getByText("Trigger sentence.")).not.toHaveClass("sr-only");
  });

  it("closes the reveal when the focused element is replaced by a different element while the wrapper stays mounted", () => {
    const { rerender } = render(
      <HoverReveal id="desc-swap" description="Consequence sentence.">
        <a href="/posture">Trigger</a>
      </HoverReveal>,
    );
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("link").focus();
    });
    expect(description).not.toHaveClass("sr-only");
    rerender(
      <HoverReveal id="desc-swap" description="Consequence sentence.">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    expect(description).toHaveClass("sr-only");
  });

  it("keeps the reveal open across a rerender while the focused element is still the active element", () => {
    const { rerender } = renderReveal();
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(description).not.toHaveClass("sr-only");
    rerender(
      <HoverReveal id="desc-1" description="Consequence sentence.">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    expect(description).not.toHaveClass("sr-only");
  });

  it("closes the reveal when the focused element is replaced by a same-tag element instance", () => {
    const { rerender } = render(
      <HoverReveal id="desc-instance" description="Consequence sentence.">
        <button type="button" key="first">
          Trigger
        </button>
      </HoverReveal>,
    );
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(description).not.toHaveClass("sr-only");
    rerender(
      <HoverReveal id="desc-instance" description="Consequence sentence.">
        <button type="button" key="second">
          Trigger
        </button>
      </HoverReveal>,
    );
    expect(description).toHaveClass("sr-only");
  });

  it("leaves a hover-only reveal open across an unrelated rerender", async () => {
    const user = userEvent.setup();
    const { rerender } = renderReveal();
    const description = screen.getByText("Consequence sentence.");
    await user.hover(screen.getByRole("button"));
    expect(description).not.toHaveClass("sr-only");
    rerender(
      <HoverReveal id="desc-1" description="Changed consequence sentence.">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    expect(screen.getByText("Changed consequence sentence.")).not.toHaveClass("sr-only");
  });

  it("keeps a dismissed reveal closed across a later rerender", () => {
    const { rerender } = renderReveal();
    const description = screen.getByText("Consequence sentence.");
    act(() => {
      screen.getByRole("button").focus();
    });
    expect(description).not.toHaveClass("sr-only");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(description).toHaveClass("sr-only");
    rerender(
      <HoverReveal id="desc-1" description="Consequence sentence.">
        <button type="button">Trigger</button>
      </HoverReveal>,
    );
    expect(description).toHaveClass("sr-only");
  });

  it("does not strand the tooltip open after a click: pointerdown-originated focus plus pointer leave closes it", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    const button = screen.getByRole("button");
    fireEvent.pointerEnter(button);
    fireEvent.pointerDown(button);
    act(() => {
      button.focus();
    });
    expect(description).not.toHaveClass("sr-only");
    fireEvent.pointerLeave(button);
    expect(description).toHaveClass("sr-only");
  });

  it("still opens on a later keyboard-focus after a prior click, once the pointer-origin flag has been consumed", () => {
    renderReveal();
    const description = screen.getByText("Consequence sentence.");
    const button = screen.getByRole("button");
    fireEvent.pointerDown(button);
    act(() => {
      button.focus();
    });
    act(() => {
      button.blur();
    });
    act(() => {
      button.focus();
    });
    expect(description).not.toHaveClass("sr-only");
  });

  it("stops propagation on Escape so a reveal nested in an open modal closes only itself, not the modal", () => {
    const onModalEscape = vi.fn();
    render(
      // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
      <form
        onKeyDown={(e) => {
          if (e.key === "Escape") onModalEscape();
        }}
      >
        <HoverReveal id="desc-2" description="Nested consequence.">
          <button type="button">Trigger</button>
        </HoverReveal>
      </form>,
    );
    const trigger = screen.getByRole("button");
    act(() => {
      trigger.focus();
    });
    fireEvent.keyDown(trigger, { key: "Escape", bubbles: true });
    expect(screen.getByText("Nested consequence.")).toHaveClass("sr-only");
    expect(onModalEscape).not.toHaveBeenCalled();
  });
});
