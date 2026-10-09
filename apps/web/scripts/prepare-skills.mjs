import { readFile, mkdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { resolve, join } from "node:path";
import { zipSync } from "fflate";
import { spaceSkills } from "../lib/space-skills.ts";

const repositorySkills = fileURLToPath(new URL("../../../skills/", import.meta.url));
const publicSkills = fileURLToPath(new URL("../public/skills/", import.meta.url));

export async function prepareSkills(source = repositorySkills, destination = publicSkills) {
  const archives = [];
  const bundle = {};
  for (const { name } of spaceSkills) {
    const files = {};
    for (const relative of ["SKILL.md", "agents/openai.yaml"]) {
      const bytes = new Uint8Array(await readFile(join(source, name, relative)));
      files[`${name}/${relative}`] = [bytes, { mtime: new Date(2000, 0, 1) }];
    }
    archives.push([`${name}.zip`, zipSync(files, { level: 9 })]);
    Object.assign(bundle, files);
  }
  archives.push(["space-skills.zip", zipSync(bundle, { level: 9 })]);
  await rm(destination, { recursive: true, force: true });
  await mkdir(destination, { recursive: true });
  for (const [name, bytes] of archives) await writeFile(join(destination, name), bytes);
  return archives.map(([name]) => name);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const files = await prepareSkills();
  console.log(`Prepared ${files.length} Space skill archives`);
}
