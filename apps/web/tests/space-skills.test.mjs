import assert from "node:assert/strict";
import { test } from "node:test";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { unzipSync } from "fflate";
import { spaceSkills, skillInstallPrompt } from "../lib/space-skills.ts";
import { prepareSkills } from "../scripts/prepare-skills.mjs";

const source = new URL("../../../skills/", import.meta.url);

test("catalog covers every documented skill that ships agent metadata", async () => {
  const readme = await readFile(new URL("README.md", source), "utf8");
  const documented = [...readme.matchAll(/\[([a-z-]+)\]\(\1\/SKILL\.md\)/g)].map((match) => match[1]);
  // A download pairs SKILL.md with agents/openai.yaml, so a documented skill that has no
  // metadata yet is not offered until it does; adding the file makes this test ask for it.
  const downloadable = documented.filter((name) => existsSync(new URL(`${name}/agents/openai.yaml`, source)));
  assert.ok(downloadable.length >= 16);
  assert.deepEqual(spaceSkills.map(({ name }) => name).sort(), downloadable.sort());
  for (const skill of spaceSkills) {
    const metadata = await readFile(new URL(`${skill.name}/agents/openai.yaml`, source), "utf8");
    assert.ok(metadata.includes(`display_name: "${skill.title}"`));
    assert.ok(metadata.includes(`short_description: "${skill.description}"`));
  }
});

test("downloads are deterministic exact-file archives with no extra content", async () => {
  const output = await mkdtemp(join(tmpdir(), "space-skills-"));
  try {
    await writeFile(join(output, "stale.zip"), "stale");
    const generated = await prepareSkills(undefined, output);
    assert.equal(generated.length, 17);
    assert.deepEqual((await readdir(output)).sort(), generated.sort());
    const first = new Map();
    for (const archive of generated) first.set(archive, await readFile(join(output, archive)));
    await prepareSkills(undefined, output);
    for (const archive of generated) assert.deepEqual(await readFile(join(output, archive)), first.get(archive));
    const bundle = unzipSync(first.get("space-skills.zip"));
    assert.equal(Object.keys(bundle).length, 32);
    for (const { name } of spaceSkills) {
      const files = unzipSync(first.get(`${name}.zip`));
      assert.deepEqual(Object.keys(files).sort(), [`${name}/SKILL.md`, `${name}/agents/openai.yaml`]);
      for (const [path, bytes] of Object.entries(files)) {
        assert.deepEqual(Buffer.from(bytes), await readFile(new URL(path, source)));
        assert.deepEqual(bundle[path], bytes);
      }
    }
  } finally {
    await rm(output, { recursive: true, force: true });
  }
});

test("installation prompt uses the selected URL and preserves user control", () => {
  for (const { name } of spaceSkills) {
    const url = `https://example.test/skills/${name}.zip`;
    const prompt = skillInstallPrompt(name, url);
    assert.ok(prompt.startsWith(`Install only the ${name} skill from ${url}.`));
    for (const text of [`${name}/SKILL.md`, `${name}/agents/openai.yaml`, "actual supported", "without my approval", "do not write anything to Space", "report the failure honestly", "Verify the installed files"]) assert.ok(prompt.includes(text));
  }
  assert.throws(() => skillInstallPrompt("unknown", "https://example.test/skills/unknown.zip"));
  assert.throws(() => skillInstallPrompt("space-init", "https://example.test/skills/space-tasks.zip"));
  assert.throws(() => skillInstallPrompt("space-init", "file:///skills/space-init.zip"));
});
