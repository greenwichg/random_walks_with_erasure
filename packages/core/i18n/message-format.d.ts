/**
 * Type contract for `message-format.js`.
 *
 * The implementation is plain ESM JavaScript on purpose — see the header of that file — so this
 * declaration is what gives `core.ts` (and everything downstream of it) the types. Two exports,
 * both pure; keep this in step with the JSDoc next door.
 */

/** The argument names a message needs: plain `{name}` placeholders plus each plural block's selector. */
export declare function messageArgs(template: string): Set<string>;

/** Expand every `{arg, plural, …}` block against `params`, leaving everything else untouched. */
export declare function expandPlurals(
  template: string,
  params: Record<string, unknown>,
  lang: string,
  formatNumber: (n: number) => string,
): string;
