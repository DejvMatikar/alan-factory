import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownBodyProps {
  children: string;
  className?: string;
  measure?: "prose" | "full";
}

interface NodeProps { children?: ReactNode }
interface LinkProps extends NodeProps { href?: string | undefined }

const REMARK_PLUGINS = [remarkGfm];

function heading(className: string) {
  return ({ children }: NodeProps) => <h6 className={className}>{children}</h6>;
}

const MD_COMPONENTS = {
  h1: ({ children }: NodeProps) => (
    <h4 className="mb-1 mt-3 text-xl font-bold">{children}</h4>
  ),
  h2: ({ children }: NodeProps) => (
    <h5 className="mb-1 mt-2 text-lg font-bold">{children}</h5>
  ),
  h3: ({ children }: NodeProps) => (
    <h6 className="mb-1 mt-2 text-base font-semibold">{children}</h6>
  ),
  h4: heading("mb-1 mt-2 text-sm font-semibold"),
  h5: heading("mb-1 mt-2 text-xs font-semibold"),
  h6: heading("mb-1 mt-2 text-xs font-medium"),
  p: ({ children }: NodeProps) => (
    <p className="mb-2 last:mb-0">{children}</p>
  ),
  strong: ({ children }: NodeProps) => (
    <strong className="font-bold">{children}</strong>
  ),
  ul: ({ children }: NodeProps) => (
    <ul className="mb-2 list-disc space-y-0.5 pl-5">{children}</ul>
  ),
  ol: ({ children }: NodeProps) => (
    <ol className="mb-2 list-decimal space-y-0.5 pl-5">{children}</ol>
  ),
  li: ({ children }: NodeProps) => <li>{children}</li>,
  table: ({ children }: NodeProps) => (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }: NodeProps) => (
    <th className="border-b border-border px-2 py-1 text-left font-semibold">{children}</th>
  ),
  td: ({ children }: NodeProps) => (
    <td className="border-b border-border/60 px-2 py-1 align-top">{children}</td>
  ),
  pre: ({ children }: NodeProps) => (
    <pre className="mb-2 overflow-x-auto rounded border border-border bg-surface p-2 text-xs [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  code: ({ children }: NodeProps) => (
    <code className="rounded bg-surface px-1 py-0.5 font-mono text-xs">{children}</code>
  ),
  a: ({ children, href }: LinkProps) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-btn-action underline">
      {children}
    </a>
  ),
  blockquote: ({ children }: NodeProps) => (
    <blockquote className="mb-2 border-l-2 border-border pl-3 text-text-muted">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-border" />,
};

export default function MarkdownBody({
  children,
  className,
  measure = "prose",
}: Readonly<MarkdownBodyProps>) {
  const measureClass = measure === "full" ? "w-full" : "max-w-prose";
  const wrapperClassName = className === undefined ? measureClass : `${measureClass} ${className}`;
  return (
    <div className={wrapperClassName}>
      <ReactMarkdown components={MD_COMPONENTS} remarkPlugins={REMARK_PLUGINS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
