# Shared User Space

The Shared User Space is the persistent folder `/home/desktop/Shared User Space`,
backed by the desktop-home volume. It is shared by every authenticated browser
session, because all sessions use the same desktop account.

## Adding files

Open the authenticated route:

```text
https://<your-tailnet-host>/shared/
```

The route uses the same Caddy basic authentication as the desktop. It accepts
images and UTF-8 text:

- `.jpg` and `.jpeg` — JPEG
- `.png` — PNG
- `.webp` — WebP
- `.txt` — UTF-8 text, at most 1 MiB

The uploader checks image signatures and text decoding, not only the extension.
It rejects SVG, GIF, HTML, binary files, and other formats. Limits: 25 MiB per
image, 1 MiB per text file, 10 files per upload, 100 MiB per request. Existing
names are never overwritten; duplicates receive a `-1`, `-2`, … suffix.

The web route adds files only. Use the desktop file manager or a terminal to
rename or delete them. Treat uploaded content as untrusted.

## Saving lists with nano

`nano` is installed in the desktop image. Open qterminal and run:

```bash
nano "$HOME/Shared User Space/lists.txt"
```

Lists are ordinary `.txt` files in the same persistent folder and are served as
`text/plain`, never rendered as HTML.

## Saving lists with nano

`nano` is installed in the desktop image. Open qterminal and run:

```bash
nano "$HOME/Shared User Space/lists.txt"
```

Lists are ordinary text files in the same persistent folder. They are not
exposed by the `/shared/` route.

## Implementation

`scripts/shared_space.py` is the single feature script:

- `ensure --root PATH` creates or repairs storage and removes an empty `lists/`
  directory without deleting files.
- `serve --root PATH` runs the upload service.
- `patch-caddy FILE` adds the `/shared/` route idempotently.

`configure.py` and `fix-caddy.sh` call the same script, so there is no duplicate
route or storage logic.

## Existing deployments

New configurations written by `scripts/configure.py` include the `/shared/`
route. For an existing deployment, rebuild and redeploy the desktop image:

```bash
./scripts/update.sh
```

`scripts/update.sh` runs the local Caddy patch before rebuilding. A direct
`scripts/deploy.sh` refuses to continue when the route is missing. If the image
already contains this code, `./scripts/fix-caddy.sh --apply` updates only the
Caddy route and restarts the Pod.

## Security notes

- The route is behind the existing single viewer account; it is not per-user
  isolation.
- The upload service binds to `127.0.0.1:8082` inside the Pod and is reached only
  through Caddy.
- Filenames are flattened and validated; symlinks and unsupported formats are
  rejected.
- Add a delete or quota policy before exposing this to untrusted users.
