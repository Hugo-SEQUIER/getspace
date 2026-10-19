/** The side of the square picture the website uploads, in pixels. */
export const PROFILE_PHOTO_SIZE = 256;
const MAX_SOURCE_BYTES = 20 * 1024 * 1024;

// Parameters: an image file the person picked.
// What it does: crops it to a centred square and redraws it at 256 pixels. Redrawing drops
// camera metadata such as location, and keeps the upload to a few kilobytes.
// Output: a WebP data URL, or JPEG where the browser cannot encode WebP.
export async function profilePhoto(file: File): Promise<string> {
  if (file.size > MAX_SOURCE_BYTES) throw new Error("Choose a picture of at most 20 MB.");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new Error("This file is not a picture Space can read. Choose a PNG, JPEG or WebP.");
  }
  const side = Math.min(bitmap.width, bitmap.height);
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = PROFILE_PHOTO_SIZE;
  const context = canvas.getContext("2d");
  if (!context || !side) throw new Error("This picture could not be prepared. Try another one.");
  context.imageSmoothingQuality = "high";
  context.drawImage(
    bitmap,
    (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side,
    0, 0, PROFILE_PHOTO_SIZE, PROFILE_PHOTO_SIZE,
  );
  bitmap.close();
  const webp = canvas.toDataURL("image/webp", 0.86);
  return webp.startsWith("data:image/webp") ? webp : canvas.toDataURL("image/jpeg", 0.86);
}
