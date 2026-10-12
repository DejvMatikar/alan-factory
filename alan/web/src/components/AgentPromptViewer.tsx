import { useState } from "react";

import type { AgentPromptSegment } from "../api";
import {
  findMatchRanges,
  placeholderToken,
  promptPieces,
  promptPlainText,
  type PromptPiece,
} from "../lib/agentSearch";
import SectionHeading from "../ui/SectionHeading";
import Button from "./Button";

type CopyState = "idle" | "copied" | "failed";

interface Navigation {
  readonly term: string;
  readonly index: number;
  readonly navigated: boolean;
}

interface LegendItem {
  readonly key: string;
  readonly token: string;
  readonly text: string;
}

const PLACEHOLDER_CLASS =
  "rounded border border-dashed border-btn-action px-1 text-btn-action";
const SHARED_CLASS = "border-l-2 border-border pl-2";
const MARK_CLASS = "rounded-sm bg-amber-300/30 text-text-primary";
const CURRENT_MARK_CLASS = "rounded-sm bg-amber-300 text-slate-900";
const STATUS_CLASS = "inline-flex min-h-11 items-center text-xs text-text-muted";
const SHARED_BLOCK_NOTE = "Shared prompt block — the same text in every agent that uses it.";
const PLACEHOLDER_FALLBACK_NOTE = "Filled in with a run-time value.";

function scrollMarkIntoView(element: HTMLElement | null): void {
  element?.scrollIntoView({ block: "center" });
}

async function copyText(text: string): Promise<CopyState> {
  try {
    await navigator.clipboard.writeText(text);
    return "copied";
  } catch {
    return "failed";
  }
}

function useMatchNavigation(term: string, count: number) {
  const [nav, setNav] = useState<Navigation>({ term, index: 0, navigated: false });
  const active = nav.term === term ? nav : { term, index: 0, navigated: false };
  const index = Math.min(active.index, Math.max(count - 1, 0));
  const move = (step: number): void => {
    setNav({ term, index: (index + step + count) % count, navigated: true });
  };
  return {
    index,
    navigated: active.navigated,
    next: () => {
      move(1);
    },
    prev: () => {
      move(-1);
    },
  };
}

function legendItems(segments: readonly AgentPromptSegment[]): LegendItem[] {
  const items = new Map<string, LegendItem>();
  for (const segment of segments) {
    if (segment.kind === "placeholder") {
      items.set(`placeholder:${segment.label}`, {
        key: `placeholder:${segment.label}`,
        token: placeholderToken(segment.label),
        text: segment.description ?? PLACEHOLDER_FALLBACK_NOTE,
      });
    } else if (segment.shared) {
      items.set(`shared:${segment.label}`, {
        key: `shared:${segment.label}`,
        token: segment.label,
        text: SHARED_BLOCK_NOTE,
      });
    }
  }
  return [...items.values()];
}

function pieceClass(piece: PromptPiece): string {
  if (piece.segment.kind === "placeholder") return PLACEHOLDER_CLASS;
  return piece.segment.shared ? SHARED_CLASS : "";
}

function SharedBlockLabel({ piece, testId }: Readonly<{ piece: PromptPiece; testId: string }>) {
  if (!piece.segmentStart || piece.segment.kind !== "text" || !piece.segment.shared) return null;
  return (
    <span className="select-none text-text-muted" data-testid={`${testId}-shared-label`}>
      {`[shared block: ${piece.segment.label}]\n`}
    </span>
  );
}

interface MatchMarkProps {
  readonly text: string;
  readonly isCurrent: boolean;
  readonly isAnchor: boolean;
  readonly scrollToCurrent: boolean;
  readonly testId: string;
}

function MatchMark({ text, isCurrent, isAnchor, scrollToCurrent, testId }: Readonly<MatchMarkProps>) {
  const anchored = isCurrent && isAnchor;
  return (
    <mark
      className={isCurrent ? CURRENT_MARK_CLASS : MARK_CLASS}
      data-testid={`${testId}-match`}
      data-current={anchored ? "true" : undefined}
      ref={anchored && scrollToCurrent ? scrollMarkIntoView : undefined}
    >
      {text}
    </mark>
  );
}

interface PieceViewProps {
  readonly piece: PromptPiece;
  readonly current: number | null;
  readonly scrollToCurrent: boolean;
  readonly testId: string;
}

function PieceView({ piece, current, scrollToCurrent, testId }: Readonly<PieceViewProps>) {
  return (
    <>
      <SharedBlockLabel piece={piece} testId={testId} />
      <span className={pieceClass(piece)}>
        {piece.match === null ? (
          piece.text
        ) : (
          <MatchMark
            text={piece.text}
            isCurrent={piece.match === current}
            isAnchor={piece.matchStart}
            scrollToCurrent={scrollToCurrent}
            testId={testId}
          />
        )}
      </span>
    </>
  );
}

interface ToolbarProps {
  readonly term: string;
  readonly count: number;
  readonly index: number;
  readonly copyState: CopyState;
  readonly testId: string;
  readonly onPrev: () => void;
  readonly onNext: () => void;
  readonly onCopy: () => void;
}

const COPY_STATUS: Readonly<Record<CopyState, string>> = {
  idle: "",
  copied: "Copied to the clipboard",
  failed: "Copy failed — select the text and copy it manually",
};

function MatchNavigation({
  term,
  count,
  index,
  testId,
  onPrev,
  onNext,
}: Readonly<Omit<ToolbarProps, "copyState" | "onCopy">>) {
  if (term === "") return null;
  return (
    <>
      <span
        role="status"
        aria-live="polite"
        className={STATUS_CLASS}
        data-testid={`${testId}-status`}
      >
        {count === 0 ? `No matches for “${term}” in this prompt` : `Match ${String(index + 1)} of ${String(count)}`}
      </span>
      {count > 0 && (
        <>
          <Button variant="ghost" onClick={onPrev} data-testid={`${testId}-prev`}>
            Previous match
          </Button>
          <Button variant="ghost" onClick={onNext} data-testid={`${testId}-next`}>
            Next match
          </Button>
        </>
      )}
    </>
  );
}

function ViewerToolbar(props: Readonly<ToolbarProps>) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <MatchNavigation {...props} />
      <Button variant="ghost" onClick={props.onCopy} data-testid={`${props.testId}-copy`}>
        Copy
      </Button>
      <span
        role="status"
        aria-live="polite"
        className={STATUS_CLASS}
        data-testid={`${props.testId}-copy-status`}
      >
        {COPY_STATUS[props.copyState]}
      </span>
    </div>
  );
}

function PromptLegend({ items, testId }: Readonly<{ items: readonly LegendItem[]; testId: string }>) {
  if (items.length === 0) return null;
  return (
    <ul className="space-y-1 text-xs text-text-muted" data-testid={`${testId}-legend`}>
      {items.map((item) => (
        <li key={item.key}>
          <span className="font-mono text-text-primary">{item.token}</span> — {item.text}
        </li>
      ))}
    </ul>
  );
}

export interface AgentPromptViewerProps {
  readonly segments: readonly AgentPromptSegment[];
  readonly title: string;
  readonly testId: string;
  readonly term?: string;
}

export default function AgentPromptViewer({
  segments,
  title,
  testId,
  term = "",
}: Readonly<AgentPromptViewerProps>) {
  const needle = term.trim();
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const plain = promptPlainText(segments);
  const ranges = findMatchRanges(plain, needle);
  const { index, navigated, next, prev } = useMatchNavigation(needle, ranges.length);
  const pieces = promptPieces(segments, ranges);
  const onCopy = (): void => {
    void copyText(plain).then(setCopyState);
  };

  return (
    <section className="space-y-2" aria-label={title} data-testid={testId}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-h-11 items-center">
          <SectionHeading level="h4">{title}</SectionHeading>
        </div>
        <ViewerToolbar
          term={needle}
          count={ranges.length}
          index={index}
          copyState={copyState}
          testId={testId}
          onPrev={prev}
          onNext={next}
          onCopy={onCopy}
        />
      </div>
      <pre
        className="whitespace-pre-wrap break-words rounded-md border border-border bg-surface px-3 py-2 font-mono text-xs text-text-primary"
        data-testid={`${testId}-text`}
      >
        {pieces.map((piece, position) => (
          <PieceView
            key={`${String(position)}:${String(piece.match)}`}
            piece={piece}
            current={ranges.length === 0 ? null : index}
            scrollToCurrent={navigated}
            testId={testId}
          />
        ))}
      </pre>
      <PromptLegend items={legendItems(segments)} testId={testId} />
    </section>
  );
}
