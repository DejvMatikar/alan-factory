interface NavigatorWithUserAgentData {
  readonly userAgent: string;
  readonly userAgentData?: { readonly platform?: string };
}

const APPLE_PLATFORM_PATTERN = /mac|iphone|ipad|ipod/i;

export function isApplePlatform(): boolean {
  const nav: NavigatorWithUserAgentData = navigator;
  return APPLE_PLATFORM_PATTERN.test(nav.userAgentData?.platform ?? nav.userAgent);
}
