# Shared User Space

The Shared User Space is the persistent folder `/home/desktop/Shared User Space`,
backed by the desktop-home volume. It is shared by every authenticated browser
session, because all sessions use the same desktop account.

## Adding images

Open the authenticated route:

```text
https://<your-tailnet-host>/shared/
```

The route uses the same Caddy basic authentication as the desktop. It accepts
only:

- `.jpg` and `.jpeg` — JPEG
- `.png` — PNG
- `.webp` — WebP

The uploader also checks the file signature, not only the extension. It rejects
SVG, GIF, HTML, and other formats. Limits: 25 MiB per file, 10 files per upload,
100 MiB per request. Existing names are never overwritten; duplicates receive a
`-1`, `-2`, … suffix.

The web route only adds images. Use the desktop file manager or a terminal to
rename or delete them. Treat uploaded images as untrusted content.

## Saving lists with nano

`nano` is installed in the desktop image. Open qterminal and run:

```bash
nano "$HOME/Shared User Space/lists.txt"
```

Lists are ordinary text files in the same persistent folder. They are not
exposed by the `/shared/` route.

## Existing deployments

New configurations written by `scripts/configure.py` include the `/shared/`
route. For an existing deployment, update the Caddyfile and restart the Pod:

```bash
./scripts/fix-caddy.sh --apply
```

`scripts/update.sh` runs the local Caddy patch before rebuilding. A direct
`scripts/deploy.sh` refuses to continue when the route is missing.

## Security notes

- The route is behind the existing single viewer account; it is not per-user
  isolation.
- The upload service binds to `127.0.0.1:8082` inside the Pod and is reached only
  through Caddy.
- Filenames are flattened and validated; symlinks and unsupported formats are
  rejected.
- Add a delete or quota policy before exposing this to untrusted users.
