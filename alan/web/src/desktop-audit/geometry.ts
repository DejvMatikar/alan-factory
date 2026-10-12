export interface GeometryRect {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface GeometryNode {
  readonly rect: GeometryRect;
  readonly firstTextRect: GeometryRect | null;
  readonly signature: string;
}

export interface ActionGroupCandidate {
  readonly path: string;
  readonly contentWidthPx: number;
  readonly columnGapPx: number;
  readonly children: readonly GeometryNode[];
}

export interface RepeatingGroupCandidate {
  readonly path: string;
  readonly rows: readonly (readonly GeometryNode[])[];
}

export interface GeometryTree {
  readonly actionGroups: readonly ActionGroupCandidate[];
  readonly repeatingGroups: readonly RepeatingGroupCandidate[];
}

export interface GeometryOptions {
  readonly minRepeatingGroupSize: number;
  // Omitted in the real browser call: page.evaluate() only ever passes the
  // JSON-serializable opts object, never a live DOM node, so the walk
  // defaults to the browser's own `document` global rather than requiring a
  // root to be threaded across the Node/browser boundary. Tests pass an
  // explicit root to scope extraction to one fixture without needing to
  // clear the rest of the document between assertions.
  readonly root?: ParentNode;
}

// The whole extraction walk is nested INSIDE this one function (not split
// across module-level helpers), and it takes exactly one argument, so that
// `extractGeometryTree.toString()` is a fully self-contained function body
// with no references to this module's scope — the desktop-audit e2e spec
// hands this function straight to page.evaluate(opts), which serializes and
// re-executes only the function it is given (single-argument, per
// page.evaluate's own contract), never its module's other declarations.
export function extractGeometryTree(opts: GeometryOptions): GeometryTree {
  const root: ParentNode = opts.root ?? document;

  function rectOf(el: Element): GeometryRect {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height };
  }

  function isVisible(el: Element): boolean {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function visibleChildren(el: Element): Element[] {
    return Array.from(el.children).filter(isVisible);
  }

  function signatureOf(el: Element): string {
    const classes = Array.from(el.classList)
      .sort((a, b) => a.localeCompare(b))
      .join(".");
    return `${el.tagName.toLowerCase()}.${classes}`;
  }

  function hasOwnText(el: Element): boolean {
    return Array.from(el.childNodes).some(
      (n) => n.nodeType === 3 && (n as Text).data.trim().length > 0,
    );
  }

  function findFirstTextElement(el: Element): Element | null {
    if (hasOwnText(el)) return el;
    for (const child of Array.from(el.children)) {
      const found = findFirstTextElement(child);
      if (found) return found;
    }
    return null;
  }

  function firstTextRectOf(el: Element): GeometryRect | null {
    const found = findFirstTextElement(el);
    return found ? rectOf(found) : null;
  }

  function nodeOf(el: Element): GeometryNode {
    return { rect: rectOf(el), firstTextRect: firstTextRectOf(el), signature: signatureOf(el) };
  }

  // No "|| row" fallback: getComputedStyle always resolves flex-direction to
  // its initial value ("row") when unset, in both real browsers and jsdom —
  // style.flexDirection is never the empty string, so a fallback here would
  // be untestable dead code.
  //
  // isRowFlexContainer, parsePx, parseGapPx, and contentWidthOf below take
  // only their own parameters. A generic linter reads that as free to hoist
  // to module scope, but they stay nested: extractGeometryTree.toString()
  // must remain a fully self-contained function body for page.evaluate (see
  // the comment above this function) — hoisting any of them out would throw
  // ReferenceError in the browser. NOSONAR suppresses the "move to outer
  // scope" suggestion on each.
  function isRowFlexContainer(style: CSSStyleDeclaration): boolean { // NOSONAR
    const display = style.display;
    if (display !== "flex" && display !== "inline-flex") return false;
    return style.flexDirection === "row" || style.flexDirection === "row-reverse";
  }

  // No "|| 0px" default: a real browser's computed style always resolves a
  // length property (gap, padding, border-width) to a concrete value
  // ("normal" for an unset gap), never the empty string, so
  // parseFloat("normal") reliably yields NaN and the Number.isFinite guard
  // below is what actually supplies the 0 default.
  function parsePx(value: string): number { // NOSONAR
    const parsed = Number.parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function parseGapPx(style: CSSStyleDeclaration): number { // NOSONAR
    return parsePx(style.columnGap);
  }

  // A "none" border-style renders no border regardless of what border-width
  // resolves to (the CSSOM resolved-value special case for border-width is
  // 0 whenever border-style is none) — checked explicitly rather than
  // trusted from borderLeftWidth/borderRightWidth alone, since real
  // browsers apply that special case but jsdom's computed style does not.
  function borderInsetPx(style: CSSStyleDeclaration): number { // NOSONAR
    const leftPx = style.borderLeftStyle === "none" ? 0 : parsePx(style.borderLeftWidth);
    const rightPx = style.borderRightStyle === "none" ? 0 : parsePx(style.borderRightWidth);
    return leftPx + rightPx;
  }

  // getBoundingClientRect().width is the border box: padding and border
  // inflate it past the width actually available to lay out children,
  // which hides wrapping violations on padded action rows. Subtracting
  // horizontal padding and border width recovers the content box the
  // children are placed in.
  function contentWidthOf(el: Element, style: CSSStyleDeclaration): number { // NOSONAR
    const borderBoxWidthPx = el.getBoundingClientRect().width;
    const insetPx = parsePx(style.paddingLeft) + parsePx(style.paddingRight) + borderInsetPx(style);
    return borderBoxWidthPx - insetPx;
  }

  function describePath(el: Element, ancestry: readonly string[]): string {
    return [...ancestry, signatureOf(el)].join(" > ");
  }

  function buildActionGroupCandidate(
    el: Element,
    style: CSSStyleDeclaration,
    children: readonly Element[],
    path: string,
  ): ActionGroupCandidate {
    return {
      path,
      contentWidthPx: contentWidthOf(el, style),
      columnGapPx: parseGapPx(style),
      children: children.map(nodeOf),
    };
  }

  function repeatingChildGroups(children: readonly Element[], minSize: number): Element[][] {
    const bySignature = new Map<string, Element[]>();
    for (const child of children) {
      const sig = signatureOf(child);
      const list = bySignature.get(sig) ?? [];
      list.push(child);
      bySignature.set(sig, list);
    }
    return Array.from(bySignature.values()).filter((list) => list.length >= minSize);
  }

  function buildRepeatingGroupCandidate(path: string, rows: readonly Element[]): RepeatingGroupCandidate {
    return { path, rows: rows.map((row) => visibleChildren(row).map(nodeOf)) };
  }

  function collectFromElement(
    el: Element,
    ancestry: readonly string[],
    actionGroups: ActionGroupCandidate[],
    repeatingGroups: RepeatingGroupCandidate[],
  ): void {
    const path = describePath(el, ancestry);
    const style = getComputedStyle(el);
    const visible = visibleChildren(el);

    if (isRowFlexContainer(style) && visible.length >= 2) {
      actionGroups.push(buildActionGroupCandidate(el, style, visible, path));
    }

    for (const group of repeatingChildGroups(visible, opts.minRepeatingGroupSize)) {
      repeatingGroups.push(buildRepeatingGroupCandidate(path, group));
    }

    // Recurse through EVERY child, not just the visible ones: a zero-size
    // wrapper (a bare layout <div>, or a test scaffold element with no
    // rect stub) can still contain visible descendants — judging
    // visibility per-node for candidate-building is correct, but pruning
    // the walk at the first zero-size ancestor would silently skip
    // everything beneath it.
    const nextAncestry = [...ancestry, signatureOf(el)];
    for (const child of Array.from(el.children)) {
      collectFromElement(child, nextAncestry, actionGroups, repeatingGroups);
    }
  }

  const actionGroups: ActionGroupCandidate[] = [];
  const repeatingGroups: RepeatingGroupCandidate[] = [];
  const rootElement = "documentElement" in root ? (root as Document).documentElement : (root as Element);
  collectFromElement(rootElement, [], actionGroups, repeatingGroups);
  return { actionGroups, repeatingGroups };
}
