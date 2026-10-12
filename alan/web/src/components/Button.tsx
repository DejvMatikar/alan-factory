import { Link } from "react-router-dom";
import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  ReactNode,
} from "react";

export type ButtonVariant = "create" | "action" | "destroy" | "reset" | "ghost";

const BASE =
  "inline-flex min-h-11 min-w-11 items-center justify-center rounded-md px-3 py-1.5 text-sm font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed";

const ICON_ONLY_BASE =
  "inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-sm font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed";

const COMPACT_BASE =
  "inline-flex min-h-6 min-w-6 items-center justify-center rounded-md px-2 py-0.5 text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed";

const COMPACT_ICON_ONLY_BASE =
  "inline-flex min-h-6 min-w-6 items-center justify-center rounded-md text-xs font-medium " +
  "transition-colors focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-text-primary/60 disabled:opacity-50 disabled:cursor-not-allowed";

type ButtonDensity = "default" | "compact";

const BASES: Record<ButtonDensity, { text: string; iconOnly: string }> = {
  default: { text: BASE, iconOnly: ICON_ONLY_BASE },
  compact: { text: COMPACT_BASE, iconOnly: COMPACT_ICON_ONLY_BASE },
};

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  create: "bg-btn-create text-surface hover:bg-btn-create/90",
  action: "bg-btn-action text-surface hover:bg-btn-action/90",
  destroy: "bg-btn-destroy text-surface hover:bg-btn-destroy/90",
  reset: "bg-btn-reset text-surface hover:bg-btn-reset/90",
  ghost: "border border-border text-text-primary hover:bg-surface-elevated",
};

const ICON_ONLY_VARIANT_CLASSES: Record<ButtonVariant, string> = {
  create: "text-btn-create hover:bg-btn-create/15",
  action: "text-btn-action hover:bg-btn-action/15",
  destroy: "text-btn-destroy hover:bg-btn-destroy/15",
  reset: "text-btn-reset hover:bg-btn-reset/15",
  ghost: VARIANT_CLASSES.ghost,
};

interface BaseOwnProps {
  variant: ButtonVariant;
  density?: ButtonDensity;
  className?: string;
  children: ReactNode;
}

type IconOnlyDiscriminant = { iconOnly: true; "aria-label": string } | { iconOnly?: false };

type OwnProps = BaseOwnProps & IconOnlyDiscriminant;

type ButtonAsButton = OwnProps & {
  linkTo?: never;
  href?: never;
  type?: "button" | "submit" | "reset";
  busy?: boolean;
} & Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    keyof BaseOwnProps | "type"
  >;

type ButtonAsLink = OwnProps & {
  linkTo: string;
  href?: never;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof BaseOwnProps | "href">;

type ButtonAsExternalLink = OwnProps & {
  href: string;
  linkTo?: never;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof BaseOwnProps | "href">;

export type ButtonProps = ButtonAsButton | ButtonAsLink | ButtonAsExternalLink;

function buildClassName(
  variant: ButtonVariant,
  extra: string | undefined,
  iconOnly: boolean,
  density: ButtonDensity,
): string {
  const bases = BASES[density];
  const base = iconOnly ? bases.iconOnly : bases.text;
  const variantClasses = iconOnly ? ICON_ONLY_VARIANT_CLASSES[variant] : VARIANT_CLASSES[variant];
  return extra ? `${base} ${variantClasses} ${extra}` : `${base} ${variantClasses}`;
}

function renderLink(props: Readonly<ButtonAsLink>) {
  const { variant, className, linkTo, children, iconOnly, density, ...rest } = props;
  return (
    <Link
      to={linkTo}
      data-variant={variant}
      data-icon-only={iconOnly ? "true" : undefined}
      className={buildClassName(variant, className, iconOnly ?? false, density ?? "default")}
      {...rest}
    >
      {children}
    </Link>
  );
}

const BUSY_CLASS = "aria-disabled:opacity-50 aria-disabled:cursor-not-allowed";

interface BusyButtonAttrs {
  readonly ariaDisabled: true | undefined;
  readonly ariaBusy: true | undefined;
  readonly className: string;
  readonly onClick: ButtonAsButton["onClick"];
}

function busyButtonAttrs(
  busy: boolean | undefined,
  base: string,
  onClick: ButtonAsButton["onClick"],
): BusyButtonAttrs {
  if (busy !== true) {
    return { ariaDisabled: undefined, ariaBusy: undefined, className: base, onClick };
  }
  return {
    ariaDisabled: true,
    ariaBusy: true,
    className: `${base} ${BUSY_CLASS}`,
    onClick: undefined,
  };
}

function renderButton(props: Readonly<ButtonAsButton>) {
  const { variant, className, type, children, iconOnly, density, busy, onClick, ...rest } = props;
  const base = buildClassName(variant, className, iconOnly ?? false, density ?? "default");
  const busyAttrs = busyButtonAttrs(busy, base, onClick);
  return (
    <button
      type={type ?? "button"}
      data-variant={variant}
      data-icon-only={iconOnly ? "true" : undefined}
      aria-disabled={busyAttrs.ariaDisabled}
      aria-busy={busyAttrs.ariaBusy}
      className={busyAttrs.className}
      onClick={busyAttrs.onClick}
      {...rest}
    >
      {children}
    </button>
  );
}

function renderExternalLink(props: Readonly<ButtonAsExternalLink>) {
  const { variant, className, href, children, target, rel, iconOnly, density, ...rest } = props;
  return (
    <a
      href={href}
      target={target ?? "_blank"}
      rel={rel ?? "noopener noreferrer"}
      data-variant={variant}
      data-icon-only={iconOnly ? "true" : undefined}
      className={buildClassName(variant, className, iconOnly ?? false, density ?? "default")}
      {...rest}
    >
      {children}
    </a>
  );
}

export default function Button(props: Readonly<ButtonProps>) {
  if (props.href !== undefined) {
    return renderExternalLink(props);
  }
  if (props.linkTo !== undefined) {
    return renderLink(props);
  }
  return renderButton(props);
}

export function GearIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
      data-testid="icon-gear"
    >
      <path
        fillRule="evenodd"
        d="M10 6.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm-1.75 3.5a1.75 1.75 0 1 1 3.5 0 1.75 1.75 0 0 1-3.5 0Z"
        clipRule="evenodd"
      />
      <path d="M8.62 1.65a1 1 0 0 1 .95-.65h.86a1 1 0 0 1 .95.65l.37 1.03a6.5 6.5 0 0 1 1.35.55l1-.47a1 1 0 0 1 1.15.2l.6.6a1 1 0 0 1 .2 1.15l-.47 1a6.5 6.5 0 0 1 .55 1.35l1.03.37a1 1 0 0 1 .65.95v.86a1 1 0 0 1-.65.95l-1.03.37a6.5 6.5 0 0 1-.55 1.35l.47 1a1 1 0 0 1-.2 1.15l-.6.6a1 1 0 0 1-1.15.2l-1-.47a6.5 6.5 0 0 1-1.35.55l-.37 1.03a1 1 0 0 1-.95.65h-.86a1 1 0 0 1-.95-.65l-.37-1.03a6.5 6.5 0 0 1-1.35-.55l-1 .47a1 1 0 0 1-1.15-.2l-.6-.6a1 1 0 0 1-.2-1.15l.47-1a6.5 6.5 0 0 1-.55-1.35l-1.03-.37a1 1 0 0 1-.65-.95v-.86a1 1 0 0 1 .65-.95l1.03-.37a6.5 6.5 0 0 1 .55-1.35l-.47-1a1 1 0 0 1 .2-1.15l.6-.6a1 1 0 0 1 1.15-.2l1 .47a6.5 6.5 0 0 1 1.35-.55l.37-1.03Z" />
    </svg>
  );
}

export function CheckIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
      data-testid="icon-check"
    >
      <path
        fillRule="evenodd"
        d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export function TrashIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
      data-testid="icon-trash"
    >
      <path
        fillRule="evenodd"
        d="M8.75 1a.75.75 0 0 0-.75.75V3H4.5a.75.75 0 0 0 0 1.5h.322l.83 11.626A2.25 2.25 0 0 0 7.897 18h4.206a2.25 2.25 0 0 0 2.245-1.874L15.178 4.5h.322a.75.75 0 0 0 0-1.5H12V1.75a.75.75 0 0 0-.75-.75h-2.5ZM9.5 3V2.5h1V3h-1Zm-2.822 1.5h6.644l-.82 11.485a.75.75 0 0 1-.748.515H7.897a.75.75 0 0 1-.748-.515L6.678 4.5Z"
        clipRule="evenodd"
      />
    </svg>
  );
}

export function StopIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
      data-testid="icon-stop"
    >
      <rect x="5" y="5" width="10" height="10" rx="1.5" />
    </svg>
  );
}

function TrayArrowPath() {
  return (
    <path
      fillRule="evenodd"
      d="M10 2a1 1 0 0 1 1 1v7.086l1.293-1.293a1 1 0 1 1 1.414 1.414l-3 3a1 1 0 0 1-1.414 0l-3-3a1 1 0 1 1 1.414-1.414L9 10.086V3a1 1 0 0 1 1-1ZM4 13a1 1 0 0 1 1-1h1a1 1 0 1 1 0 2H6v2h8v-2h-.5a1 1 0 1 1 0-2H15a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4Z"
      clipRule="evenodd"
    />
  );
}

export function ArchiveIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4"
      aria-hidden="true"
      data-testid="icon-archive"
    >
      <TrayArrowPath />
    </svg>
  );
}

export function UnarchiveIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
      className="h-4 w-4 rotate-180"
      aria-hidden="true"
      data-testid="icon-unarchive"
    >
      <TrayArrowPath />
    </svg>
  );
}
