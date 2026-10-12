import { useEffect, useLayoutEffect, useRef } from "react";

export function useEscapeToStop(thinking: boolean, onStop: () => void): void {
  const latest = useRef({ thinking, onStop });
  useLayoutEffect(() => {
    latest.current = { thinking, onStop };
  });

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent): void {
      if (e.key === "Escape" && latest.current.thinking) {
        latest.current.onStop();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);
}
