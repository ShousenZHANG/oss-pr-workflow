# Java / Kotlin defect checklist

For scouting defects in recently merged code (phase 2) and for reviewing your own change (phase 4). Report a defect only when you can show the input that triggers it.

## Null and boundaries
- Dereferencing values from maps, optional fields, deserialized JSON or legacy APIs without a null check; `Optional.get()` without `isPresent`.
- `list.get(0)`, `iterator().next()` on possibly empty collections; off-by-one in `subList` and loops.
- Integer overflow in sums and multiplications of sizes, times and money; `int` division where a fraction was meant.

## Equality and collections
- `==` on strings or boxed numbers; `equals` overridden without `hashCode`.
- Mutating a collection while iterating it; returning internal mutable collections from getters.

## Errors and resources
- Exceptions caught and ignored, or rethrown without the cause.
- Streams, connections, readers not closed on every path (use try-with-resources).

## Concurrency
- Shared mutable fields without synchronization; check-then-act on `ConcurrentHashMap` instead of `computeIfAbsent`.
- `SimpleDateFormat` and other non-thread-safe objects held in static fields.
- Double-checked locking without `volatile`.

## Security
- SQL, LDAP or shell commands concatenated from input; XML parsers without XXE protection.
- Deserialization of untrusted data; secrets logged.

## Kotlin specifics
- `!!` on platform types from Java; `lateinit` accessed before initialization; `runBlocking` on request threads.

## Before reporting
- Check annotations (`@NonNull`, `@Nullable`) and callers; look for a test that pins the behaviour.
