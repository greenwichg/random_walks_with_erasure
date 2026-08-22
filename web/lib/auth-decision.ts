// Compatibility shim. The implementation moved to @ih/core so the Expo app can share it;
// this keeps every existing `@/lib/auth-decision` import working, unchanged.
//
// New code should import from "@ih/core/logic/auth-decision" directly. These shims are deleted when the last
// call site does — see docs/CORE_MIGRATION_MAP.md.
export * from "@ih/core/logic/auth-decision";
