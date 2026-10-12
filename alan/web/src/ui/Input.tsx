import type {
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from "react";

const BASE = "w-full rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-text-primary";
const TAP_TARGET_CLASS = "min-h-11";
const FOCUS_RING_CLASS =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60";
const PLACEHOLDER_CLASS = "placeholder:text-text-muted";
const DISABLED_CLASS = "disabled:opacity-50";

interface BaseFieldChromeProps {
  className?: string | undefined;
  tapTarget?: boolean | undefined;
  focusRing?: boolean | undefined;
  disabledStyle?: boolean | undefined;
}

interface ChromeOptions extends BaseFieldChromeProps {
  placeholderStyle?: boolean | undefined;
}

function buildClassName(opts: Readonly<ChromeOptions>): string {
  const tokens: string[] = [];
  if (opts.className !== undefined && opts.className !== "") tokens.push(opts.className);
  if (opts.tapTarget === true) tokens.push(TAP_TARGET_CLASS);
  tokens.push(BASE);
  if (opts.focusRing === true) tokens.push(FOCUS_RING_CLASS);
  if (opts.placeholderStyle === true) tokens.push(PLACEHOLDER_CLASS);
  if (opts.disabledStyle !== false) tokens.push(DISABLED_CLASS);
  return tokens.join(" ");
}

type SharedOmitKeys = keyof BaseFieldChromeProps | "children";

type InputAsInput = BaseFieldChromeProps & {
  as: "input";
  placeholderStyle?: boolean | undefined;
  children?: never;
} & Omit<InputHTMLAttributes<HTMLInputElement>, SharedOmitKeys>;

type InputAsTextarea = BaseFieldChromeProps & {
  as: "textarea";
  placeholderStyle?: boolean | undefined;
  children?: never;
} & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, SharedOmitKeys>;

type InputAsSelect = BaseFieldChromeProps & {
  as: "select";
  placeholderStyle?: never;
  children: ReactNode;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, SharedOmitKeys>;

type InputProps = InputAsInput | InputAsTextarea | InputAsSelect;

function renderInput(props: Readonly<InputAsInput>) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- as is destructured only to keep it out of the DOM spread
  const { as, className, tapTarget, focusRing, disabledStyle, placeholderStyle, ...rest } = props;
  return (
    <input
      className={buildClassName({ className, tapTarget, focusRing, disabledStyle, placeholderStyle })}
      {...rest}
    />
  );
}

function renderTextarea(props: Readonly<InputAsTextarea>) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- as is destructured only to keep it out of the DOM spread
  const { as, className, tapTarget, focusRing, disabledStyle, placeholderStyle, ...rest } = props;
  return (
    <textarea
      className={buildClassName({ className, tapTarget, focusRing, disabledStyle, placeholderStyle })}
      {...rest}
    />
  );
}

function renderSelect(props: Readonly<InputAsSelect>) {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- as is destructured only to keep it out of the DOM spread
  const { as, className, tapTarget, focusRing, disabledStyle, children, ...rest } = props;
  return (
    <select className={buildClassName({ className, tapTarget, focusRing, disabledStyle })} {...rest}>
      {children}
    </select>
  );
}

export default function Input(props: Readonly<InputProps>) {
  if (props.as === "select") return renderSelect(props);
  if (props.as === "textarea") return renderTextarea(props);
  return renderInput(props);
}
