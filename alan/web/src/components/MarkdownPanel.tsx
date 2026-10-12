import { useState } from "react";

import Disclosure from "../ui/Disclosure";
import MarkdownBody from "./MarkdownBody";
import MarkdownSourceToggle from "./MarkdownSourceToggle";

export interface MarkdownPanelProps {
  title: string;
  children: string;
  defaultOpen?: boolean;
  className?: string;
  contentClassName?: string;
  ariaLabel: string;
  testId: string;
}

export default function MarkdownPanel({
  title,
  children,
  defaultOpen = true,
  className,
  contentClassName,
  ariaLabel,
  testId,
}: Readonly<MarkdownPanelProps>) {
  const [showSource, setShowSource] = useState(false);

  return (
    <Disclosure
      preset="markdown"
      defaultOpen={defaultOpen}
      className={className}
      testId={`${testId}-disclosure`}
      label={<span>{title}</span>}
    >
      <div className="mt-2 space-y-2">
        <MarkdownSourceToggle
          showSource={showSource}
          onToggle={() => {
            setShowSource((prev) => !prev);
          }}
          testId={`${testId}-source-toggle`}
        />
        <section aria-label={ariaLabel} data-testid={testId} className={contentClassName}>
          {showSource ? (
            <pre className="whitespace-pre-wrap break-words">{children}</pre>
          ) : (
            <MarkdownBody measure="full">{children}</MarkdownBody>
          )}
        </section>
      </div>
    </Disclosure>
  );
}
