/* Explicit worker, query and transport operating budgets. */
export const SEARCH_BUDGETS = Object.freeze({
  logical: 8 * 1024 ** 3, chunk: 1024 ** 2, manifest: 4 * 1024 ** 2,
  decoded: 32 * 1024 ** 2, compressed: 8 * 1024 ** 2,
  requests: 32768, transfer: 512 * 1024 ** 2, queryMilliseconds: 300000,
  sqliteHeap: 96 * 1024 ** 2, wasmHeap: 128 * 1024 ** 2
});
