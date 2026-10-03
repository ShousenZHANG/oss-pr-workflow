# Python defect checklist

For scouting defects in recently merged code (phase 2) and for reviewing your own change (phase 4). Report a defect only when you can show the input that triggers it; "looks risky" is not a finding.

## Boundaries
- Indexing a sequence a caller supplies without an empty check: `xs[0]`, `xs[-1]`, `max(xs)`, `min(xs)`, unpacking `a, b = xs`.
- `None` from an optional return, a `.get()`, or a default reaching attribute access or arithmetic. Find the producer before calling it a bug.
- Off-by-one in slices and ranges at the first and last element; integer division and zero divisors.
- `d[key]` where the key can be absent; `.get()` where `None` is then used as a real value.

## Errors
- `except:` or `except Exception:` swallowing errors with `pass`, or catching more than the guarded call can raise.
- `raise NewError(...)` inside `except` without `from err`, losing the cause.
- `assert` used to validate input (removed under `python -O`).

## State and identity
- Mutable default arguments (`def f(x=[])`); class-level mutable attributes meant per instance.
- Closures over a loop variable all seeing its last value.
- `is` comparing strings or numbers (works by accident through interning).

## Resources and async
- Files, sockets, locks or sessions opened without `with` and leaked on an early return or exception.
- Blocking calls (`time.sleep`, `requests`, file I/O) inside `async def`; tasks created and never awaited.
- Check-then-act on shared state across threads without a lock.

## Security
- `eval`/`exec`, `pickle.load`, `yaml.load` without `SafeLoader` on data from outside.
- `subprocess` with `shell=True` and interpolated input; SQL built with f-strings.
- Paths joined from user input without normalization; secrets in logs.

## Before reporting
- Read the whole function, its docstring and the comment above it: deliberate behaviour is often documented.
- Look for a test that asserts the current behaviour.
- Check type hints and callers: an "impossible" input may be ruled out upstream.
