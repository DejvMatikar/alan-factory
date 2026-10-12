import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import HoverReveal from "./HoverReveal";
import { NAV_COUNT_PILL_CLASS } from "./navCountPill";

export const NAV_LINK_CHROME_CLASS =
  "inline-flex min-h-11 min-w-11 items-center gap-1.5 rounded-md px-2 text-sm font-medium text-text-primary hover:bg-surface-elevated";

export interface DescribedNavLinkProps {
  readonly to: string;
  readonly ariaLabel: string;
  readonly testId: string;
  readonly description: string;
  readonly count?: number | null;
  readonly leading?: ReactNode;
  readonly align?: "start" | "end";
  readonly children: ReactNode;
}

export default function DescribedNavLink({
  to,
  ariaLabel,
  testId,
  description,
  count = null,
  leading,
  align = "start",
  children,
}: Readonly<DescribedNavLinkProps>) {
  const descriptionId = `${testId}-description`;
  return (
    <HoverReveal id={descriptionId} description={description} align={align}>
      <Link
        to={to}
        aria-label={ariaLabel}
        data-testid={testId}
        className={NAV_LINK_CHROME_CLASS}
      >
        {leading}
        {children}
        {count !== null && count > 0 && (
          <span data-testid={`${testId}-count`} className={NAV_COUNT_PILL_CLASS}>
            {count}
          </span>
        )}
      </Link>
    </HoverReveal>
  );
}
