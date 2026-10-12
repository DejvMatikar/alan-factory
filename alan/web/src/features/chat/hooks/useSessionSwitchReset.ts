import { useEffect, useRef } from "react";

export function useSessionSwitchReset(
  sessionId: string | null,
  onSwitch: () => void,
): void {
  const prevRef = useRef<string | null>(sessionId);
  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = sessionId;
    if (prev !== null && sessionId !== null && prev !== sessionId) onSwitch();
  }, [sessionId, onSwitch]);
}
