import { afterEach, describe, expect, it } from "vitest";

describe("jsdom 29 runtime", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("element.click() dispatches a PointerEvent (not a MouseEvent)", () => {
    const button = document.createElement("button");
    document.body.appendChild(button);

    let captured: Event | null = null;
    button.addEventListener("click", (e) => {
      captured = e;
    });
    button.click();

    expect(captured).not.toBeNull();
    expect(captured).toBeInstanceOf(PointerEvent);
    expect((captured as unknown as Event).type).toBe("click");
  });

  it("getComputedStyle returns a populated CSSStyleDeclaration (new CSSOM)", () => {
    const div = document.createElement("div");
    div.style.color = "rgb(10, 20, 30)";
    document.body.appendChild(div);
    const computed = window.getComputedStyle(div);
    expect(computed.color).toBe("rgb(10, 20, 30)");
  });
});
