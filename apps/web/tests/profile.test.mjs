import assert from "node:assert/strict";
import test from "node:test";
import { avatarInitials, avatarTone } from "../lib/space/profile.ts";

test("initials handle full names, email addresses, empty values, and Unicode", () => {
  assert.equal(avatarInitials("Alex Morgan"), "AM");
  assert.equal(avatarInitials("jamie.chen@example.com"), "JC");
  assert.equal(avatarInitials(" "), "?");
  assert.equal(avatarInitials("김민수"), "김");
  assert.equal(avatarInitials("Émile Zola"), "ÉZ");
  assert.equal(avatarTone(" Alex Morgan "), avatarTone("alex morgan"));
});
