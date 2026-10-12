import type { ReactNode } from "react";

const BANNER = "rounded-md bg-banner-error-tint/15 px-3 py-2 text-xs text-banner-error-fg";
const ACTION_ROW = "flex items-center justify-between gap-3 ";

export default function ErrorBanner({
  children,
  action,
  "data-testid": dataTestId,
}: Readonly<{
  children: ReactNode;
  action?: ReactNode;
  "data-testid"?: string;
}>) {
  if (action !== undefined) {
    return (
      <div role="alert" data-testid={dataTestId} className={ACTION_ROW + BANNER}>
        <span>{children}</span>
        {action}
      </div>
    );
  }
  return (
    <div role="alert" data-testid={dataTestId} className={BANNER}>
      {children}
    </div>
  );
}
