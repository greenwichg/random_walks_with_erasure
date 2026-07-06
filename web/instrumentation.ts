/**
 * Next.js instrumentation hook — runs once when the server boots (not during `next build`), the
 * right place to fail fast on a misconfigured production deployment. In production a missing or
 * inconsistent required variable exits the process (refuse to boot) rather than letting the app
 * come up and fail at the first sign-in or engine call. Outside production nothing is fatal, so
 * local development and the Colab demo keep working with zero configuration.
 *
 * Enabled via `experimental.instrumentationHook` in next.config.mjs (Next 14).
 */
export async function register() {
  // Only the Node.js server runtime validates process env (skip the edge runtime / build).
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const { validateEnv, formatReport } = await import("./lib/env-validation.mjs");
  const report = validateEnv(process.env);
  const { warnLines, errorLines } = formatReport(report);

  for (const line of warnLines) console.warn(line);

  if (report.errors.length > 0) {
    const bar = "=".repeat(74);
    console.error(
      `\n${bar}\n[env] Refusing to start — invalid configuration (${report.errors.length} problem(s)):\n\n` +
        errorLines.join("\n") +
        `\n\nFix the above, or unset RWE_ENV for local development.\n${bar}\n`,
    );
    // production => hard stop before serving; non-production never reaches here (errors are prod-gated).
    if (report.production) process.exit(1);
  }
}
