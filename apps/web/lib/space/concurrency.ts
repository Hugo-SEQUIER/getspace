/** Bound outstanding reads while preserving input order and surfacing any failure. */
export async function mapConcurrent<T, R>(
  items: readonly T[],
  operation: (item: T) => Promise<R>,
  concurrency = 4,
): Promise<R[]> {
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new Error("Invalid concurrency");
  const results = new Array<R>(items.length);
  let cursor = 0, failed = false, failure: unknown;
  async function worker() {
    while (!failed && cursor < items.length) {
      const index = cursor++;
      try { results[index] = await operation(items[index]); }
      catch (error) { if (!failed) failure = error; failed = true; }
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
  if (failed) throw failure;
  return results;
}
