import {
  cloneElement,
  isValidElement,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { REVEAL_Z_CLASS } from "../shell/stickyOffsets";

export interface HoverRevealProps {
  readonly id: string;
  readonly description: string;
  readonly children: ReactNode;
  readonly align?: "start" | "end";
  readonly wrapperRole?: "presentation";
}

type DescribableElement = ReactElement<{ "aria-describedby"?: string }>;

function withDescribedBy(children: ReactNode, id: string): ReactNode {
  if (!isValidElement<{ "aria-describedby"?: string }>(children)) return children;
  return cloneElement(children as DescribableElement, { "aria-describedby": id });
}

function isEscapeSwallowed(wrapper: HTMLSpanElement | null): boolean {
  const openDialog = document.querySelector("dialog[open]");
  if (openDialog !== null && !openDialog.contains(wrapper)) return false;
  return true;
}

function descriptionClassName(open: boolean, align: "start" | "end" | undefined): string {
  if (!open) return "sr-only";
  const alignClass = align === "end" ? "right-0" : "left-0";
  return `absolute ${alignClass} top-full ${REVEAL_Z_CLASS} mt-1 w-max max-w-64 rounded-md border border-border bg-surface-elevated px-2 py-1 text-xs text-text-muted shadow-sm`;
}

export default function HoverReveal({
  id,
  description,
  children,
  align,
  wrapperRole,
}: Readonly<HoverRevealProps>) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const open = hovered || focused;
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const focusedTargetRef = useRef<Element | null>(null);
  const pointerOriginRef = useRef(false);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useLayoutEffect(() => {
    if (focused && document.activeElement !== focusedTargetRef.current) {
      setFocused(false);
    }
  });

  useEffect(() => {
    if (!open) return;
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key !== "Escape") return;
      if (!isEscapeSwallowed(wrapperRef.current)) return;
      event.preventDefault();
      event.stopPropagation();
      setHovered(false);
      setFocused(false);
    }
    document.addEventListener("keydown", handleKeyDown, true);
    return () => {
      document.removeEventListener("keydown", handleKeyDown, true);
    };
  }, [open]);

  return (
    <span
      ref={wrapperRef}
      role={wrapperRole}
      className="relative inline-block"
      onPointerDown={() => {
        pointerOriginRef.current = true;
      }}
      onPointerEnter={() => {
        setHovered(true);
      }}
      onPointerLeave={() => {
        setHovered(false);
        pointerOriginRef.current = false;
      }}
      onFocusCapture={(event) => {
        const fromPointer = pointerOriginRef.current;
        pointerOriginRef.current = false;
        focusedTargetRef.current = event.target;
        if (!fromPointer) setFocused(true);
      }}
      onBlurCapture={() => {
        setFocused(false);
      }}
    >
      {withDescribedBy(children, id)}
      <span id={id} role="tooltip" className={descriptionClassName(open, align)}>
        {description}
      </span>
    </span>
  );
}
