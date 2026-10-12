import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Negative control for the isolation pin (src/test/isolation-pin.test.ts):
// restoreMocks/unstubGlobals/unstubEnvs are left unset, and clearMocks is
// switched off explicitly because vitest 5 turned it on by default — leaving
// it out would let a framework default supply the very reset this config
// exists to withhold. setup.no-isolation.ts also skips
// installIsolationResets()'s vi.useRealTimers() afterEach, so the pin's three
// ordered pairs must fail under this config. See package.json's
// test:pin-control script, which asserts that they do.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    clearMocks: false,
    setupFiles: ["./src/test/setup.no-isolation.ts"],
    include: ["src/test/isolation-pin.test.ts"],
  },
});
