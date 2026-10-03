# Rust defect checklist

For scouting defects in recently merged code (phase 2) and for reviewing your own change (phase 4). Report a defect only when you can show the input that triggers it.

## Panics in library code
- `unwrap()` / `expect()` on values that depend on input (parsing, I/O, map lookups, `first()`, `last()`).
- Indexing `v[i]` and slicing `&s[a..b]` without bounds checks; slicing strings at non-character boundaries.
- Arithmetic that overflows in release builds silently and panics in debug builds; `as` casts that truncate.

## Errors
- Errors mapped to a generic message, losing the source (`map_err(|_| ...)`); `?` converting into an error type that drops context.
- `Result` values ignored with `let _ =` where failure matters.

## Ownership and concurrency
- `clone()` added to satisfy the borrow checker where the logic needed shared state.
- `Mutex` held across `.await`; blocking calls inside async tasks.
- `unsafe` blocks without a stated invariant, or whose invariant the new code breaks.

## API and behaviour
- Public API changes (signatures, trait bounds, `#[non_exhaustive]` removal) in a patch-level change.
- `Ord` / `PartialEq` / `Hash` implementations that disagree with each other.

## Before reporting
- Read the doc comments, `debug_assert!`s and tests; check whether the caller already guarantees the invariant (types often encode it).
