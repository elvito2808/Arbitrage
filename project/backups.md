# Project backup register — 2026-11-26

**Status: INCOMPLETE — durable owner-controlled independent copies of A and B are PENDING.**

Agent 1 ARB-005 tested a temporary Git bundle for B approved baseline `f3ba66eea85e67d86bccf4eee901b3aec483562d` in an isolated temporary restore, including `git fsck --full` and matching HEAD/tree. Temporary file SHA256: `c211cd2de7ceaf28bc7f74f38d1e9c1057c625f8b6d76c97973720c88fda7958`. Evidence in `project/evidence/ARB-005-011/backup-restore.txt`; Agent 0 inspected but did not independently reproduce the restore. No guarantee the temporary bundle survives Codex session.

P01 evidence merge commit: `b4ed121aac1249352dccf4a5d4353765e66dfee5`; A pin `cb23c4b08d2103aead243b59d6b66f325361a4bc`.

Required next: independent owner-controlled copy of both repositories and needed artifacts, in separate failure domain, checksum manifest, isolated restore, checked commit/tree and recorded result. Procedure: `project/backup-instructions.md`. Do not store binaries, sensitive paths, secrets or private files in this public repo. Agent 0 records sanitized backup location reference and verification after owner returns results.

**Until then P02 implementation changes remain gated.** Project branches/PRs alone are not independent backups.
