# Go defect checklist

For scouting defects in recently merged code (phase 2) and for reviewing your own change (phase 4). Report a defect only when you can show the input that triggers it.

## Errors
- Returned errors ignored (`_ =` or a bare call), or checked after the value is already used.
- Errors wrapped without `%w`, breaking `errors.Is` / `errors.As` for callers.
- `err` shadowed by `:=` in an inner scope, so the outer `err` returned is nil.

## Nil and boundaries
- Nil map writes; nil pointer dereference on an optional field or an interface holding a typed nil.
- Slice indexing without a length check; off-by-one in `s[i:j]`.
- Integer overflow in conversions between sizes (`int64` to `int32`), lengths and offsets.

## Concurrency
- Data races: a map or struct field written by several goroutines without a lock.
- Goroutines that never exit (blocked channel send, no context cancellation); `wg.Add` inside the goroutine.
- Loop variable captured by a goroutine or closure in Go versions before 1.22 semantics (check `go.mod`).
- `defer` inside a loop holding resources until the function returns.

## Resources
- `resp.Body`, files and rows not closed on every path; `defer Close()` before checking the error.
- Contexts not passed down, so cancellation and timeouts are lost.

## Security
- Shell commands built from input (`exec.Command("sh", "-c", ...)`); SQL built with `fmt.Sprintf`.
- Paths from input joined without `filepath.Clean` and a root check; TLS verification disabled.

## Before reporting
- Read the doc comment and tests; check whether callers already guarantee the invariant.
