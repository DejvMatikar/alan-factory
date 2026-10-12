interface UnderlineTabProps {
  id: string;
  label: string;
  active: boolean;
  onClick: () => void;
}

export default function UnderlineTab({
  id,
  label,
  active,
  onClick,
}: Readonly<UnderlineTabProps>) {
  return (
    <button
      type="button"
      id={`tab-${id}`}
      role="tab"
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      onClick={onClick}
      data-testid={`tab-${id}`}
      className={
        active
          ? "pb-2 text-sm font-medium border-b-2 border-text-primary text-text-primary"
          : "pb-2 text-sm font-medium text-text-muted hover:text-text-primary"
      }
    >
      {label}
    </button>
  );
}
