// Compatibility shim. The implementation moved to @ih/core so the Expo app can share it;
// this keeps every existing `@/services/index` import working, unchanged.
//
// New code should import from "@ih/core/api/services" directly. These shims are deleted when the last
// call site does — see docs/CORE_MIGRATION_MAP.md.
export * from "@ih/core/api/services";
