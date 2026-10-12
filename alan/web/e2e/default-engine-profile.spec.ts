import fs from "node:fs";

import { installedDefaultProfile, TEST_DEFAULT_PROFILE } from "./fixtures/default-profile";
import { expect, test } from "./fixtures/test-base";

test(
  "the worker server's engines root holds the test default profile byte for byte",
  { tag: "@no-claude" },
  ({ e2eRoot }) => {
    const installed = installedDefaultProfile(e2eRoot);
    expect(fs.existsSync(installed)).toBe(true);
    expect(fs.readFileSync(installed).equals(fs.readFileSync(TEST_DEFAULT_PROFILE))).toBe(true);
  },
);
