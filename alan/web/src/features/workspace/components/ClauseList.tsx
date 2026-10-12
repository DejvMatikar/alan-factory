import type { ReactNode } from "react";

export interface ClauseListItem {
  id: string;
  text: string;
}

interface ClauseListProps<T extends ClauseListItem> {
  items: readonly T[];
  testId: string;
  itemTestId: (item: T) => string;
  renderMarker: (item: T) => ReactNode;
}

export default function ClauseList<T extends ClauseListItem>({
  items,
  testId,
  itemTestId,
  renderMarker,
}: Readonly<ClauseListProps<T>>) {
  return (
    <ul data-testid={testId} className="space-y-1 list-none text-xs">
      {items.map((item) => (
        <li
          key={item.id}
          className="flex items-start gap-2"
          data-testid={itemTestId(item)}
        >
          {renderMarker(item)}
          <span className="text-text-primary">{item.text}</span>
        </li>
      ))}
    </ul>
  );
}
