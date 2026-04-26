# NOTES — known tech debt

These are flagged but intentionally NOT being fixed in the current
session. Pick up next time we're in this repo.

## src/app.py is duplicated end-to-end

The file currently contains its entire body twice (333 lines, every
route decorator + view function appears at lines ~50 and again at
~214). Python silently lets the second definition replace the first
on import, so the app runs — but it's a real bug:

- Anyone reading the file will be confused about which copy is canonical.
- A future edit to the "wrong" copy will look like a no-op.
- It bloats diffs and makes blame/log harder to follow.

The 2026-04-26 deploy work modified the *second* copy of `def index()`
(the live one) to render `home.html` and added a new `/tool` route.
Routes register correctly today, but the duplication should be cleaned
up before any meaningful edit to app.py.

**Fix when ready:**
1. Confirm the bottom block (with `if __name__ == "__main__":`) is the
   canonical copy.
2. Delete lines covering the first duplicate (roughly 1–211, keeping
   the module docstring + imports + helper functions intact).
3. Re-run `python -m src.app` and confirm the same 6 routes still register.

## src/app.py: hardcoded `secret_key`

`app.secret_key = "dev-secret-change-in-prod"` is committed. The app
doesn't currently use sessions or flash messages, so this isn't
exploitable today, but it is a literal warning to fix before relying
on session-based features. Move to env:

```python
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-prod")
```

…and set `FLASK_SECRET_KEY` on Render alongside `ANTHROPIC_API_KEY`.

## Render free-tier filesystem is ephemeral

`BASE_DIR/tmp` and `BASE_DIR/output` work for local dev but reset on
every container restart. For real-world use the review/approve flow
should persist to durable storage (S3, Render Disk, etc.) — not urgent
while the tool is single-user local-dev, but mention before any "share
with library partners" rollout.
