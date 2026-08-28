/**
 * Yield to the browser for one painted frame before running `task`.
 *
 * Discovery's layouts are heavy and run synchronously on the main thread:
 *  - the DFG runs the custom Celonis Sugiyama (`celonisFlowLayout`) inline;
 *  - Petri / Heuristics call `elkLayout`, and elkjs's `new ELK()`
 *    (`elk.bundled.js`) runs its GWT solver *synchronously on the main thread*
 *    behind a Promise — it is the bundled "fake worker", NOT a real Web Worker
 *    (a real one needs `workerUrl`/`workerFactory`).
 *
 * Running that compute straight out of a mount `useEffect` can block before the
 * loading skeleton has visibly painted. Scheduling it with a double
 * `requestAnimationFrame` guarantees the skeleton is on screen (and its shimmer
 * has advanced a frame) before the thread stalls, so canvas first paint is
 * bounded to ~1 frame regardless of node count. Returns a cancel fn for effect
 * cleanup.
 */
export function runAfterPaint(task: () => void): () => void {
  let inner = 0;
  const outer = requestAnimationFrame(() => {
    inner = requestAnimationFrame(task);
  });
  return () => {
    cancelAnimationFrame(outer);
    if (inner) cancelAnimationFrame(inner);
  };
}
