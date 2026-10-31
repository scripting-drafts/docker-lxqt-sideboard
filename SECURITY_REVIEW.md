# Sanitization review

This copy contains source code and synthetic examples, not live deployment state.
It is not a guarantee that all confidential information has been identified.

## Scope and checks

- Examined every regular file as UTF-8 text; checked for symlinks and binary data.
- Compared against known runtime credentials, authentication hashes, selected
  kubeconfig values, source metadata, and deployment identifiers. Tested direct,
  base64, URL-safe base64, URL-encoded, and hexadecimal representations where
  applicable. Generic words and example identifiers can cause false positives.
- Scanned every commit message and every reachable Git blob in both public
  repositories, not only the current checkout.
- Searched for common private-key, provider-token, bearer-token, JWT,
  password-hash, personal-path, email, and private Funnel hostname patterns.
- Reviewed non-loopback IPv4 addresses and high-entropy candidates. Remaining
  private addresses are synthetic test fixtures; documentation addresses and
  loopback addresses are examples or functional configuration.
- Removed the historical VPN endpoint and personal-session narrative from the
  retained setup guide. Example profile names do not identify a real account.
- Excluded live config and secrets, backups, kubeconfig, runtime data, generated
  manifests, logs, images, and the original validation history.
- No external drive was mounted, so no external archive links were created.
  Instead, the backup tool now creates checksum-pinned timeline entries and
  `latest/` symlinks for each new archive when a drive is configured.

## Final history audit

- Re-cloned both public repositories as mirrors and inspected every reachable
  blob plus explicitly fetched cached historical commits.
- Public source: 1 reachable commit, 55 files; four cached commits and 62 blobs
  checked, totaling 218,005 bytes.
- No live secret, encoded secret, 16-byte secret fragment, private-key block,
  personal path, private Funnel hostname, VPN endpoint, or fixture address was
  found. Earlier generic-word and `0.0.0.0` matches were false positives.
- The prior public repository was deleted and replaced with a clean repository.
  Checked old commit SHAs return 404. Hosting-provider internal backup retention
  is outside this project's control.

## Limitations

Pattern matching, known-value comparisons, and entropy analysis cannot identify
every secret, transformed value, personal detail, or confidential algorithm.
No external secret-scanning product or independent human review was performed.
Review all files and history before publishing; use fresh credentials if deploying.

The public repository was recreated with sanitized history and generic authorship.
Hosting providers may retain internal backups or caches beyond repository deletion;
no live secret value was found in any removed history.
