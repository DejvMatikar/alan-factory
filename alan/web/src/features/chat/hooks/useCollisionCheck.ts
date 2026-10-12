import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../../api";
import type { ChatFamily, CollisionCheckResponse, CollisionMatchInfo } from "../../../api";

export interface CollisionInfo {
  status: "loading" | "green" | "yellow" | "red";
  sentence: string;
  match: CollisionMatchInfo | null;
}

export const GREEN: CollisionInfo = {
  status: "green",
  sentence: "No overlapping work found.",
  match: null,
};

export function toInfo(resp: CollisionCheckResponse): CollisionInfo {
  return { status: resp.state, sentence: resp.sentence, match: resp.match };
}

export interface UseCollisionCheckOptions {
  auto?: boolean;
  chatFamily?: ChatFamily | undefined;
  sessionId?: string | undefined;
  onSettled?: (() => void) | undefined;
}

export function useCollisionCheck(
  initialBrief: string,
  opts: UseCollisionCheckOptions = {},
): {
  collisionInfo: CollisionInfo;
  rerun: (brief: string) => Promise<CollisionInfo>;
} {
  const { auto = true, chatFamily, sessionId, onSettled } = opts;
  const [collisionInfo, setCollisionInfo] = useState<CollisionInfo>(
    auto && initialBrief.trim() ? { status: "loading", sentence: "", match: null } : GREEN,
  );

  // A single promise chain guarantees the after-summarize auto-check and an
  // explicit manual re-run never call /api/collision-check concurrently: each
  // new check is appended to the chain and only starts once the prior one settles.
  const chainRef = useRef<Promise<unknown>>(Promise.resolve());

  const check = useCallback(
    (brief: string): Promise<CollisionInfo> => {
      const settled = chainRef.current.then(() =>
        api
          .checkCollision(brief, { chatFamily, sessionId })
          .then((resp) => {
            const info = toInfo(resp);
            setCollisionInfo(info);
            return info;
          })
          .catch(() => {
            setCollisionInfo(GREEN);
            return GREEN;
          }),
      );
      chainRef.current = settled.finally(() => {
        try {
          onSettled?.();
        } catch {
          // never let a throwing onSettled reject the chain and stall later checks
        }
      });
      return settled;
    },
    [chatFamily, sessionId, onSettled],
  );

  useEffect(() => {
    if (!auto || !initialBrief.trim()) return;
    void check(initialBrief);
  }, [initialBrief, auto, check]);

  const rerun = useCallback(
    (brief: string): Promise<CollisionInfo> => {
      if (!brief.trim()) {
        setCollisionInfo(GREEN);
        return Promise.resolve(GREEN);
      }
      setCollisionInfo({ status: "loading", sentence: "", match: null });
      return check(brief);
    },
    [check],
  );

  return { collisionInfo, rerun };
}
