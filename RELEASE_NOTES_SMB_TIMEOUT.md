# SMB stall timeouts (urlio 0.6.6)

## What changed

Directory listing and related SMB helpers no longer spin forever in
`while conn.is_busy` when the filer stops responding. They raise
`urlio.smb_timeout.SMBTimeoutError` after a configurable **stall limit**.

## Timeout semantics

- **`SMBPath(..., timeout=N)`** (default **120**, overridable via env
  **`URLIO_SMB_TIMEOUT`**) is a **stall limit**, not a cap on total listing
  duration.
- The timer resets whenever the server sends SMB traffic (pysmb completes a
  poll with data). Large, slow directories may take many minutes as long as
  listing keeps making progress within `N` seconds between responses.
- If the connection is dead and no traffic arrives for `N` seconds, listing
  (and other wrapped operations) fail fast with `SMBTimeoutError`.
- Per-packet pysmb timeouts are chunked (≤30s) but bounded by the remaining
  stall budget.
- After I/O timeout or connection errors, `SMBPath` drops its cached connection
  so the next operation opens a fresh socket.

## file-router upgrade

1. Pin **`urlio==0.6.6`** (or newer) in file-router `requirements.txt`.
2. Treat **`SMBTimeoutError`** like other source scan failures so
   `handle_failure` → sync → `completed` can run.
3. Optional: set **`URLIO_SMB_TIMEOUT`** on workers if 120s stall is too tight
   for very slow filers (still distinct from a future filerouter scan wall-clock
   backstop).
4. When adding a scan backstop in file-router, use a value **greater than**
   urlio’s stall timeout so legitimate long listings are not cut off by both
   layers at once.
