/**
 * The globals a shared module is allowed to assume exist.
 *
 * `tsconfig.json` sets `"lib": ["ES2022"]` and `"types": []`, which means TypeScript declares the
 * language and nothing else — no `document`, no `window`, and equally no `setTimeout`, no `fetch`,
 * no `console`. That is the point: a global is available here only if it is written down below, and
 * writing it down is a decision that it exists on **both** a browser and React Native / Hermes.
 *
 * So this file is the contract, not a convenience. It is short deliberately. Adding a line to it is
 * the moment to check the RN side, and a reviewer can read the whole platform surface of the shared
 * core in thirty seconds.
 *
 * Everything here is standard on Hermes with the Expo runtime, and standard in every browser the app
 * supports. Notably ABSENT, and to stay absent: `document`, `window`, `localStorage`,
 * `navigator.sendBeacon`, `process`. The guard test bans them by name too, because a
 * `declare global` slipped into a source file would otherwise re-open the door quietly.
 */

// --- timers -------------------------------------------------------------------------------------
// Return `unknown` rather than `number` (browser) or `NodeJS.Timeout` (node): a shared module must
// only ever pass the handle back to clearTimeout, never do arithmetic on it. Typing it loosely is
// what stops a module from accidentally depending on which runtime it is in.
declare function setTimeout(handler: () => void, timeout?: number): unknown;
declare function clearTimeout(handle: unknown): void;
declare function setInterval(handler: () => void, timeout?: number): unknown;
declare function clearInterval(handle: unknown): void;

// --- console ------------------------------------------------------------------------------------
declare const console: {
  log(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
  info(...args: unknown[]): void;
  debug(...args: unknown[]): void;
};

// --- fetch and friends --------------------------------------------------------------------------
// Present in every supported browser and in Hermes. `RequestInit`/`Response` are declared minimally:
// the shared core builds requests and reads status/JSON, and nothing here should need more.
interface RequestInit {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
  cache?: string;
  credentials?: string;
  keepalive?: boolean;
}
interface Response {
  readonly ok: boolean;
  readonly status: number;
  readonly statusText: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}
declare function fetch(input: string, init?: RequestInit): Promise<Response>;

interface AbortSignal {
  readonly aborted: boolean;
  addEventListener(type: "abort", listener: () => void): void;
  removeEventListener(type: "abort", listener: () => void): void;
}
interface AbortController {
  readonly signal: AbortSignal;
  abort(reason?: unknown): void;
}
declare const AbortController: { new (): AbortController };

// --- URL ----------------------------------------------------------------------------------------
// Standard in browsers; provided by Expo's runtime (`react-native-url-polyfill` is in the default
// template). Declared here so a shared module can parse a URL without pulling in DOM lib.
interface URLSearchParams {
  get(name: string): string | null;
  getAll(name: string): string[];
  has(name: string): boolean;
  set(name: string, value: string): void;
  append(name: string, value: string): void;
  delete(name: string): void;
  forEach(cb: (value: string, key: string) => void): void;
  toString(): string;
  [Symbol.iterator](): IterableIterator<[string, string]>;
}
declare const URLSearchParams: {
  new (init?: string | Record<string, string> | [string, string][]): URLSearchParams;
};
interface URL {
  href: string;
  origin: string;
  protocol: string;
  host: string;
  hostname: string;
  port: string;
  pathname: string;
  search: string;
  hash: string;
  readonly searchParams: URLSearchParams;
  toString(): string;
}
declare const URL: { new (url: string, base?: string): URL };
