import type { ReactNode } from "react";

const HEADING = "text-xs font-semibold uppercase tracking-wide text-text-muted";
const TRUNCATE = "min-w-0 truncate ";

export default function SectionHeading({
  children,
  level,
  truncate,
  "data-testid": dataTestId,
}: Readonly<{
  children: ReactNode;
  level: "h2" | "h3" | "h4" | "h5";
  truncate?: boolean;
  "data-testid"?: string;
}>) {
  const Tag = level;
  return (
    <Tag data-testid={dataTestId} className={truncate === true ? TRUNCATE + HEADING : HEADING}>
      {children}
    </Tag>
  );
}
