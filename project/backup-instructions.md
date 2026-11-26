# Owner-controlled independent backup — preparation and acceptance

Status: PENDING. The Codex session's temporary bundle was restored, but it is not independently durable. Owner stores copies of both framework A and project B in a location outside the GitHub repository and outside any ephemeral Codex environment, ideally a physically distinct disk or independently retained cloud drive (no new paid service).

Required captures: A approved commit `cb23c4b08d2103aead243b59d6b66f325361a4bc`; B P01 acceptance merge `b4ed121aac1249352dccf4a5d4353765e66dfee5` or a newer verified main commit. Preserve full Git history/refs, a SHA256 manifest, and an isolated restoration test of both captured commits. Never commit the bundle to public B. Avoid sensitive local files.

In WSL with Git available, choose an owner-controlled destination **in a separate failure domain**; for example a mounted removable disk. Use an existing directory and verify it has free capacity. Execute in a fresh directory and save the sanitized results: the bundle filenames, checksums, captured commit IDs, clone/restore success and storage type, but not confidential local paths.

```bash
set -euo pipefail
BACKUP_DIR="/path/to/separate-owner-controlled-storage"
test -d "$BACKUP_DIR"
test -w "$BACKUP_DIR"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for item in "A|Agent-Management-Framework|cb23c4b08d2103aead243b59d6b66f325361a4bc" "B|Arbitrage|b4ed121aac1249352dccf4a5d4353765e66dfee5"; do
  IFS='|' read -r id name sha <<< "$item"
  git clone "https://github.com/elvito2808/$name.git" "$tmp/source-$id"
  git -C "$tmp/source-$id" cat-file -e "$sha^{commit}"
  bundle="$BACKUP_DIR/$name-$sha.bundle"
  test ! -e "$bundle"
  git -C "$tmp/source-$id" bundle create "$bundle" --all
  git clone "$bundle" "$tmp/restore-$id"
  git -C "$tmp/restore-$id" cat-file -e "$sha^{commit}"
  git -C "$tmp/restore-$id" checkout --detach "$sha"
  git -C "$tmp/restore-$id" fsck --full
  test "$(git -C "$tmp/source-$id" rev-parse "$sha^{tree}")" = "$(git -C "$tmp/restore-$id" rev-parse "$sha^{tree}")"
  echo "$id restore VERIFIED: $sha"
  sha256sum "$bundle" > "$bundle.sha256"
  sha256sum -c "$bundle.sha256"
done
```

This proves integrity at the moment of execution; ongoing durability and failure-domain separation still depend on the chosen storage location. Report sanitized results to Agent 0. After verification, Agent 0 records the manifest reference, restore date, locations by non-sensitive description, and releases the P02 backup gate.
