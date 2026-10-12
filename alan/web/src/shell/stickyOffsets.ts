export const TOP_BAR_REM = 4;
export const MAIN_PAD_TOP_REM = 2;
export const FRAME_PAD_REM = 1;

export const STICKY_SLOT_TOP_CLASS = "sm:top-24";
export const STICKY_FRAME_CHILD_TOP_CLASS = "sm:top-28";
export const RAIL_MAX_H_CLASS = "max-h-[calc(100dvh-7rem)]";

export const TABLE_HEADER_Z_CLASS = "sm:z-10";
export const STICKY_REGION_Z_CLASS = "sm:z-20";
export const REVEAL_Z_CLASS = "z-30";
export const TOP_BAR_Z_CLASS = "sm:z-40";

export const STICKY_REGION_CLASS = "sm:sticky sm:top-28 bg-surface sm:z-20";

export const SCROLL_COLUMN_CLASS = "sm:flex sm:min-h-0 sm:flex-col sm:max-h-[calc(100dvh-7rem)]";
export const SCROLL_BODY_SIZE_CLASS = "sm:min-h-0 sm:flex-1";
export const SCROLL_BODY_CLASS = `${SCROLL_BODY_SIZE_CLASS} sm:overflow-y-auto`;

export const SCROLL_COLUMN_FLOW_CLASS = "sm:flex sm:flex-col";
export const SCROLL_BODY_FLOW_CLASS = "";

export const JUMP_TO_LATEST_CLASS =
  `fixed bottom-4 left-1/2 -translate-x-1/2 border border-border bg-surface-elevated ` +
  `pb-[env(safe-area-inset-bottom)] ${REVEAL_Z_CLASS}`;
