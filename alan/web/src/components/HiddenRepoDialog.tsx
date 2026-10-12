import { useEffect, useState } from "react";

import { api, type EngineSummary } from "../api";
import { errorMessageOf } from "../lib/errorMessage";
import Button from "./Button";
import HoverReveal from "./HoverReveal";
import { handleModalKeyDown } from "./modalKeys";
import { useModalLifecycle } from "../ui/useModalLifecycle";

export const HIDDEN_REPO_DISCLOSURE_SUMMARY_ID = "hidden-repo-disclosure-summary";

export interface HiddenRepoAnnouncement {
  readonly text: string;
  readonly tone: "ok" | "warn" | "error";
}

const hiddenRepoAnnouncementListeners = new Set<(announcement: HiddenRepoAnnouncement) => void>();

export function announceHiddenRepoChange(announcement: HiddenRepoAnnouncement): void {
  hiddenRepoAnnouncementListeners.forEach((listener) => {
    listener(announcement);
  });
}

function subscribeHiddenRepoAnnouncement(
  listener: (announcement: HiddenRepoAnnouncement) => void,
): () => void {
  hiddenRepoAnnouncementListeners.add(listener);
  return () => {
    hiddenRepoAnnouncementListeners.delete(listener);
  };
}

export interface HiddenRepoDialogProps {
  readonly hiddenEngines: readonly EngineSummary[];
  readonly hiddenCount: number;
}

function repoKeyOf(engine: EngineSummary): string {
  return `${engine.repo.owner}/${engine.repo.repo}`;
}

type RestoreState =
  | { readonly status: "restoring" }
  | { readonly status: "error"; readonly detail: string };

interface RestoreRowProps {
  readonly engine: EngineSummary;
  readonly state: RestoreState | undefined;
  readonly onRestore: (engine: EngineSummary) => void;
}

function RestoreRow({ engine, state, onRestore }: Readonly<RestoreRowProps>) {
  const repoKey = repoKeyOf(engine);
  return (
    <li
      data-testid={`hidden-repo-row-${repoKey}`}
      className="flex flex-wrap items-center justify-between gap-2 py-1.5"
    >
      <span className="text-sm text-text-primary">{repoKey}</span>
      <span className="flex items-center gap-2">
        <Button
          variant="ghost"
          data-testid={`hidden-repo-restore-${repoKey}`}
          disabled={state?.status === "restoring"}
          onClick={() => {
            onRestore(engine);
          }}
        >
          {state?.status === "restoring" ? "Restoring…" : "Restore"}
        </Button>
        {state?.status === "error" && (
          <span className="text-xs text-btn-destroy">{state.detail}</span>
        )}
      </span>
    </li>
  );
}

const noop = (): void => undefined;

interface DialogBodyProps {
  readonly hiddenEngines: readonly EngineSummary[];
  readonly hiddenCount: number;
  readonly onClose: () => void;
}

function HiddenRepoDialogBody({
  hiddenEngines,
  hiddenCount,
  onClose,
}: Readonly<DialogBodyProps>) {
  const dialogRef = useModalLifecycle();
  const [restoreState, setRestoreState] = useState<Record<string, RestoreState>>({});

  useEffect(() => {
    /* v8 ignore next */
    if (dialogRef.current === null) return;
    if (dialogRef.current.contains(document.activeElement)) return;
    const nextRestoreButton = dialogRef.current.querySelector<HTMLElement>(
      'button[data-testid^="hidden-repo-restore-"]',
    );
    const closeButton = dialogRef.current.querySelector<HTMLElement>(
      'button[data-testid="hidden-repo-dialog-close"]',
    );
    (nextRestoreButton ?? closeButton)?.focus();
  }, [hiddenEngines, dialogRef]);

  const handleRestore = (engine: EngineSummary): void => {
    const repoKey = repoKeyOf(engine);
    setRestoreState((prev) => ({ ...prev, [repoKey]: { status: "restoring" } }));
    api.unhideEngine(repoKey).then(
      () => {
        announceHiddenRepoChange({ text: `${repoKey} restored.`, tone: "ok" });
        setRestoreState((prev) => {
          const next = { ...prev };
          delete next[repoKey];
          return next;
        });
      },
      (err: unknown) => {
        announceHiddenRepoChange({ text: `${repoKey} failed to restore.`, tone: "error" });
        setRestoreState((prev) => ({
          ...prev,
          [repoKey]: { status: "error", detail: errorMessageOf(err) },
        }));
      },
    );
  };

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions -- backdrop click-to-close; Escape is handled separately below
    <dialog
      ref={dialogRef}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === dialogRef.current) onClose();
      }}
      aria-label="Hidden repositories"
      data-testid="hidden-repo-dialog"
      className="fixed inset-0 m-0 flex h-screen w-screen items-center justify-center bg-surface/80 p-0"
    >
      {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
      <div
        onKeyDown={(e) => {
          handleModalKeyDown(e, { onEscape: onClose, onCtrlEnter: noop });
        }}
        className="max-h-[85vh] w-[90vw] max-w-md overflow-y-auto rounded-lg border border-border bg-surface-elevated p-6 shadow-xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-text-primary">
            Hidden repositories ({hiddenCount})
          </h2>
          <Button variant="ghost" data-testid="hidden-repo-dialog-close" aria-label="Close" onClick={onClose}>
            ✕
          </Button>
        </div>
        {hiddenCount === 0 ? (
          <p className="text-sm text-text-muted" data-testid="hidden-repo-empty">
            No repositories are hidden.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {hiddenEngines.map((engine) => (
              <RestoreRow
                key={repoKeyOf(engine)}
                engine={engine}
                state={restoreState[repoKeyOf(engine)]}
                onRestore={handleRestore}
              />
            ))}
          </ul>
        )}
      </div>
    </dialog>
  );
}

export default function HiddenRepoDialog({
  hiddenEngines,
  hiddenCount,
}: Readonly<HiddenRepoDialogProps>) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <HoverReveal
        id="hidden-repo-dialog-trigger-description"
        description="Repositories you've hidden — reopen this to restore one."
        align="end"
      >
        <Button
          variant="ghost"
          id={HIDDEN_REPO_DISCLOSURE_SUMMARY_ID}
          data-testid="hidden-repo-dialog-trigger"
          aria-label={`Hidden repositories (${String(hiddenCount)})`}
          onClick={() => {
            setOpen(true);
          }}
        >
          {`Hidden (${String(hiddenCount)})`}
        </Button>
      </HoverReveal>
      {open && (
        <HiddenRepoDialogBody
          hiddenEngines={hiddenEngines}
          hiddenCount={hiddenCount}
          onClose={() => {
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function announcementTextClass(tone: HiddenRepoAnnouncement["tone"]): string {
  if (tone === "ok") return "text-text-primary";
  if (tone === "warn") return "font-medium text-status-warning-fg";
  return "font-medium text-btn-destroy";
}

export function HiddenRepoAnnouncementRegion() {
  const [announcement, setAnnouncement] = useState<HiddenRepoAnnouncement | null>(null);

  useEffect(() => subscribeHiddenRepoAnnouncement(setAnnouncement), []);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-testid="hidden-repo-announcement"
      className={
        announcement === null
          ? "sr-only"
          : "flex items-center justify-between gap-2 border-b border-border bg-surface-elevated px-6 py-2 text-sm text-text-primary"
      }
    >
      {announcement !== null && (
        <>
          <span className={announcementTextClass(announcement.tone)}>{announcement.text}</span>
          <Button
            variant="ghost"
            data-testid="hidden-repo-announcement-dismiss"
            aria-label="Dismiss"
            onClick={() => {
              setAnnouncement(null);
            }}
          >
            ✕
          </Button>
        </>
      )}
    </div>
  );
}
