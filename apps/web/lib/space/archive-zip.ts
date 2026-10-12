// Space archives download as a ZIP holding one space.archive.v1 JSON file. The JSON is
// compressed as each export page arrives, so the browser keeps only compressed bytes and
// the archive is not limited by how much text a page can hold in memory.

/** fflate writes ZIP without ZIP64 extensions, whose sizes end at 4 GiB. */
const ZIP_LIMIT = 2 ** 32 - 1;

export async function archiveZip(name: string) {
  const { Zip, ZipDeflate, strToU8 } = await import("fflate");
  const chunks: Uint8Array[] = [];
  let failure: Error | null = null;
  const zip = new Zip((error, chunk) => {
    if (error) failure = error;
    else chunks.push(chunk);
  });
  const file = new ZipDeflate(name, { level: 6 });
  zip.add(file);
  let bytes = 0;
  function check() {
    if (failure) throw failure;
  }
  return {
    write(text: string) {
      const data = strToU8(text);
      bytes += data.length;
      if (bytes > ZIP_LIMIT)
        throw new Error(
          "This archive exceeds 4 GiB. Contact the installation operator for a full export.",
        );
      file.push(data);
      check();
    },
    finish() {
      file.push(new Uint8Array(0), true);
      zip.end();
      check();
      return new Blob(chunks as BlobPart[], { type: "application/zip" });
    },
  };
}

/** The archive JSON from a downloaded ZIP, or from a plain JSON file. */
export async function readArchive(file: Blob, limit: number) {
  const data = new Uint8Array(await file.arrayBuffer());
  if (data[0] !== 0x50 || data[1] !== 0x4b) return new TextDecoder().decode(data);
  const { unzipSync, strFromU8 } = await import("fflate");
  const tooLarge = () => new Error(`Choose an archive of at most ${limit / 1024 / 1024} MiB.`);
  let found = 0;
  let files: Record<string, Uint8Array>;
  try {
    // Sizes come from the ZIP directory, so an oversized archive is refused unexpanded.
    files = unzipSync(data, {
      filter(entry) {
        if (!entry.name.toLowerCase().endsWith(".json")) return false;
        if (++found > 1) throw new Error("Choose a ZIP that holds one Space archive.");
        if (entry.originalSize > limit) throw tooLarge();
        return true;
      },
    });
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("Choose")) throw error;
    throw new Error("This ZIP could not be read. Download the export again.");
  }
  const [json] = Object.values(files);
  if (!json) throw new Error("This ZIP holds no Space archive.");
  if (json.length > limit) throw tooLarge();
  return strFromU8(json);
}
