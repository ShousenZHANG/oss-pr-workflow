# TypeScript / JavaScript defect checklist

For scouting defects in recently merged code (phase 2) and for reviewing your own change (phase 4). Report a defect only when you can show the input that triggers it.

## Types that lie
- `as` casts and non-null assertions (`!`) on values that can be `undefined` at runtime (API responses, `find()`, optional props).
- `any` flowing from `JSON.parse`, `catch (e)`, or untyped libraries into code that assumes a shape.
- `==` comparisons relying on coercion; `||` defaulting where `0`, `""` or `false` are valid values (use `??`).

## Boundaries
- `arr[0]`, `arr.at(-1)`, `reduce` without an initial value, `Math.max(...arr)` on possibly empty arrays.
- Off-by-one in `slice`/`substring`; `parseInt` without a radix; `NaN` propagating from `Number()`.
- Object keys from user input used for lookup (`obj[key]`) reaching prototype properties.

## Async
- Promises not awaited (fire-and-forget) whose rejection goes unhandled.
- `forEach` with an `async` callback (does not wait); `await` inside loops that should run in parallel, or `Promise.all` where order matters.
- Race between state updates; stale closures in React effects and callbacks (missing dependencies).

## React and UI
- Hooks called conditionally; effects without cleanup for subscriptions and timers.
- Keys from array index on lists that reorder; state mirrored from props and never resynced.

## Security
- `innerHTML` / `dangerouslySetInnerHTML` with unsanitized input; URLs built from input without validation.
- Secrets in client bundles; `eval` / `new Function` on data.

## Before reporting
- Check the declared types and every caller: a "possible undefined" may be ruled out by the call sites.
- Look for a test or a comment that pins the current behaviour.
