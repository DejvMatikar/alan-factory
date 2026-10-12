import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { errorMessageOf } from "../../../lib/errorMessage";

export function useUrlDraftSession(
  create: () => Promise<{ session_id: string }>,
  enabled = true,
): { sessionId: string | null; bootstrapError: string | null; onCleared: (newId: string) => void } {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const urlSessionId = searchParams.get("s");

  const [sessionId, setSessionId] = useState<string | null>(urlSessionId);
  const [bootstrapError, setBootstrapError] = useState<string | null>(null);

  const withSession = useCallback(
    (id: string): string => {
      const next = new URLSearchParams(searchParams);
      next.set("s", id);
      return `?${next.toString()}`;
    },
    [searchParams],
  );

  // Read the latest back-fill/navigate helpers at resolution time without making
  // them create-effect dependencies: benign searchParams churn must not re-run
  // the create effect, or each re-run would POST another empty session.
  const withSessionRef = useRef(withSession);
  const navigateRef = useRef(navigate);
  useEffect(() => {
    withSessionRef.current = withSession;
    navigateRef.current = navigate;
  }, [withSession, navigate]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSessionId(urlSessionId);
    setBootstrapError(null);
  }, [urlSessionId]);

  useEffect(() => {
    if (!enabled || urlSessionId !== null) return;
    let cancelled = false;
    create()
      .then(({ session_id }) => {
        if (cancelled) return;
        Promise.resolve(
          navigateRef.current(withSessionRef.current(session_id), { replace: true }),
        ).catch(
          /* v8 ignore next */
          () => undefined,
        );
        setSessionId(session_id);
      })
      .catch((err: unknown) => {
        /* v8 ignore next */
        if (!cancelled) setBootstrapError(errorMessageOf(err));
      });
    return () => {
      cancelled = true;
    };
  }, [urlSessionId, enabled, create]);

  const onCleared = useCallback(
    (newId: string) => {
      Promise.resolve(navigate(withSession(newId), { replace: true })).catch(
        /* v8 ignore next */
        () => undefined,
      );
    },
    [navigate, withSession],
  );

  return { sessionId, bootstrapError, onCleared };
}
