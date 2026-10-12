import { useEffect, useState } from "react";

import { useHealthContext } from "../api/HealthProvider";
import type { SpendCapStop } from "../api/HealthProvider";
import { useQueuePauseStatus } from "../hooks/useQueuePauseStatus";
import { formatDate } from "../lib/format";
import QueueResumeButton from "./QueueResumeButton";

function formatResumeTime(epochSeconds: number): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(epochSeconds * 1000));
}

function formatCountdown(epochSeconds: number, nowMs: number): string {
  const secsLeft = Math.max(0, Math.ceil(epochSeconds - nowMs / 1000));
  const mm = Math.floor(secsLeft / 60);
  const ss = String(secsLeft % 60).padStart(2, "0");
  return `${String(mm)}:${ss}`;
}

const RESUMED_ANNOUNCEMENT = "Queue resumed.";
const FAILED_ANNOUNCEMENT = "Couldn't resume the queue. Try again.";

interface SpendCapStopBannerProps {
  stop: SpendCapStop;
  onResumed: () => void;
  onFailed: () => void;
}

function SpendCapStopBanner({ stop, onResumed, onFailed }: Readonly<SpendCapStopBannerProps>) {
  const regainAt = stop.regain_at ?? null;
  const regainKnown = regainAt !== null && !Number.isNaN(Date.parse(regainAt));
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="queue-stopped-banner"
      className="border-b border-border bg-surface-elevated px-6 py-2 text-sm text-text-primary"
    >
      <span className="font-medium text-btn-reset">Queue stopped</span>
      {` — ${stop.cause} ${stop.fix}`}
      {regainKnown && ` Access returns ${formatDate(regainAt)}.`}
      {" "}
      <QueueResumeButton onResumed={onResumed} onFailed={onFailed} />
    </div>
  );
}

export default function QueuePausedBanner() {
  const { queuePaused, pausedUntil, spendCapStop } = useQueuePauseStatus();
  const { refetch } = useHealthContext();
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [announcement, setAnnouncement] = useState("");
  const [prevQueuePaused, setPrevQueuePaused] = useState(queuePaused);

  if (queuePaused !== prevQueuePaused) {
    setPrevQueuePaused(queuePaused);
    if (queuePaused) setAnnouncement("");
  }

  const ticking = queuePaused && spendCapStop === null;

  useEffect(() => {
    if (!ticking) return undefined;
    const init = setTimeout(() => {
      setNowMs(Date.now());
    }, 0);
    const id = setInterval(() => {
      setNowMs(Date.now());
    }, 1000);
    return () => {
      clearTimeout(init);
      clearInterval(id);
    };
  }, [ticking]);

  const handleResumed = () => {
    setAnnouncement(RESUMED_ANNOUNCEMENT);
    refetch();
  };

  const handleFailed = () => {
    setAnnouncement(FAILED_ANNOUNCEMENT);
  };

  return (
    <>
      <p
        role="status"
        aria-live="polite"
        className="sr-only"
        data-testid="queue-resume-announcement"
      >
        {announcement}
      </p>
      {spendCapStop !== null && (
        <SpendCapStopBanner stop={spendCapStop} onResumed={handleResumed} onFailed={handleFailed} />
      )}
      {spendCapStop === null && queuePaused && pausedUntil !== null && (
        <div
          role="status"
          aria-live="polite"
          data-testid="queue-paused-banner"
          className="border-b border-border bg-surface-elevated px-6 py-2 text-sm text-text-primary"
        >
          <span className="font-medium text-btn-reset">Queue paused</span>
          {" — auto-resumes at "}
          {formatResumeTime(pausedUntil)}
          {" (in "}
          {formatCountdown(pausedUntil, nowMs)}
          {") "}
          <QueueResumeButton onResumed={handleResumed} onFailed={handleFailed} />
        </div>
      )}
    </>
  );
}
