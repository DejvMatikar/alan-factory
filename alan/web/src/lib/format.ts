export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function _pluralize(n: number, unit: string): string {
  return n === 1 ? `1 ${unit} ago` : `${n} ${unit}s ago`;
}

export function formatWarmupDuration(seconds: number): string {
  const totalTenths = Math.round(seconds * 10);
  if (totalTenths < 600) return `${(totalTenths / 10).toFixed(1)}s`;
  const totalSeconds = Math.round(seconds);
  const minutes = Math.floor(totalSeconds / 60);
  const remainder = totalSeconds - minutes * 60;
  return `${minutes}m ${String(remainder).padStart(2, "0")}s`;
}

const _MS_PER_DAY = 86_400_000;

export function formatAutoArchiveCountdown(
  updatedAt: string,
  autoArchiveDays: number | null | undefined,
): string | null {
  if (autoArchiveDays == null) return null;
  const updated = Date.parse(updatedAt);
  if (Number.isNaN(updated)) return null;
  const deadline = updated + autoArchiveDays * _MS_PER_DAY;
  const remainingMs = deadline - Date.now();
  if (remainingMs < _MS_PER_DAY) return "auto-archives soon";
  const remainingDays = Math.floor(remainingMs / _MS_PER_DAY);
  return `auto-archives in ${remainingDays} day${remainingDays === 1 ? "" : "s"}`;
}

const DURATION_EMPTY = "—";

export function formatActiveDuration(seconds: number | null | undefined): string {
  if (seconds == null) return DURATION_EMPTY;
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes === 0) return seconds > 0 ? "<1m" : "0m";
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return hours === 0 ? `${String(minutes)}m` : `${String(hours)}h ${String(minutes)}m`;
}

export function formatCostUsd(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

export function formatRelativeTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Unknown";
  const diffMs = Date.now() - d.getTime();
  if (diffMs < 0) return "Just now";
  const diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return _pluralize(diffMin, "minute");
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return _pluralize(diffHr, "hour");
  return _pluralize(Math.floor(diffHr / 24), "day");
}
