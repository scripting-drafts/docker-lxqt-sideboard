#!/usr/bin/env python3
"""Shared User Space: uploads, storage repair, and Caddy route repair."""
from __future__ import annotations

import argparse
import email.errors
import email.parser
import email.policy
import hmac
import html
import os
import pathlib
import re
import secrets
import stat
import sys
import tempfile
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
TEXT_EXTENSIONS = {'.txt'}
ALLOWED_EXTENSIONS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS
MIME_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.txt': 'text/plain; charset=utf-8',
}
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 1 * 1024 * 1024
MAX_REQUEST_BYTES = 100 * 1024 * 1024
MAX_FILES = 10
NAME_RE = re.compile(
    r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}\.(?:jpe?g|png|webp|txt)$',
    re.IGNORECASE,
)
CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
README_TEXT = """Shared User Space

Images: .jpg .jpeg .png .webp via the authenticated /shared/ web route.
Text: .txt via the same route; lists are ordinary .txt files.
Limits: 25 MiB per image, 1 MiB per text file, 10 files per upload.
Edit a list in qterminal:
  nano "$HOME/Shared User Space/lists.txt"
This folder persists with the desktop home volume.
"""


def safe_filename(raw: str | None) -> str | None:
    """Return a flat, supported filename or None."""
    if not raw:
        return None
    name = raw.replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not NAME_RE.fullmatch(name):
        return None
    if name in ('.', '..') or name.startswith('.'):
        return None
    return name


def image_kind(data: bytes) -> str | None:
    """Identify supported image bytes; never trust the extension alone."""
    if data.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp'
    return None


def text_ok(data: bytes) -> bool:
    """Accept bounded UTF-8 text without NUL or unexpected controls."""
    if len(data) > MAX_TEXT_BYTES:
        return False
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return False
    if '\x00' in text:
        return False
    return all(ord(char) >= 32 or char in '\n\r\t' for char in text)


def extension_matches(name: str, kind: str) -> bool:
    suffix = pathlib.PurePath(name).suffix.lower()
    if kind == '.jpg':
        return suffix in ('.jpg', '.jpeg')
    return suffix == kind


def file_kind(name: str, data: bytes) -> str | None:
    """Return the validated kind for a supported filename, or None."""
    suffix = pathlib.PurePath(name).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        kind = image_kind(data)
        return kind if kind and extension_matches(name, kind) else None
    if suffix in TEXT_EXTENSIONS:
        return '.txt' if text_ok(data) else None
    return None


def store_file(root: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    """Atomically add a file without overwriting an existing name."""
    stem, suffix = os.path.splitext(name)
    fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=root)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        for index in range(0, 10000):
            candidate = root / (name if index == 0 else f'{stem}-{index}{suffix}')
            if os.path.lexists(candidate):
                continue
            try:
                os.link(temporary, candidate)
            except FileExistsError:
                continue
            return candidate
        raise RuntimeError('too many files with the same name')
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def ensure_space(root: pathlib.Path) -> pathlib.Path:
    """Create or repair the Shared User Space without following symlinks."""
    root = pathlib.Path(root)
    if root.is_symlink():
        raise RuntimeError('Shared User Space must not be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o755)
    lists = root / 'lists'
    if lists.is_symlink():
        lists.unlink()
    elif lists.is_dir():
        try:
            lists.rmdir()  # only when empty; never delete stored lists
        except OSError:
            pass
    readme = root / 'README.txt'
    if not readme.exists() and not readme.is_symlink():
        readme.write_text(README_TEXT, encoding='utf-8')
        os.chmod(readme, 0o644)
    cutoff = time.time() - 3600
    for stale in root.glob('.upload-*'):
        try:
            if stale.is_file() and not stale.is_symlink() and stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            pass
    return root


def parse_multipart(content_type: str, body: bytes) -> tuple[str | None, list[tuple[str, bytes]]]:
    if not content_type.lower().startswith('multipart/form-data'):
        raise ValueError('expected multipart/form-data')
    message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b'Content-Type: ' + content_type.encode('latin-1') + b'\r\n'
        b'MIME-Version: 1.0\r\n\r\n' + body
    )
    if not message.is_multipart():
        raise ValueError('malformed multipart body')
    token = None
    files: list[tuple[str, bytes]] = []
    for part in message.iter_parts():
        if part.get_content_disposition() != 'form-data':
            continue
        field = part.get_param('name', header='content-disposition')
        payload = part.get_payload(decode=True)
        if field == 'csrf' and payload is not None:
            token = payload.decode('utf-8', 'replace')
        elif field in ('files', 'file'):
            filename = part.get_filename()
            if filename and payload is not None:
                files.append((filename, payload))
    return token, files


def render_page(root: pathlib.Path, token: str, message: str = '', status: int = 200) -> tuple[bytes, int]:
    items = []
    try:
        paths = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        paths = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            continue
        name = path.name
        url = '/shared/files/' + quote(name, safe='')
        label = html.escape(name)
        if suffix == '.txt':
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            items.append(
                '<li class="file"><a href="{url}"><span class="type">TXT</span>'
                '<span>{label}</span><small>{size} B</small></a></li>'.format(
                    url=html.escape(url, quote=True), label=label, size=size,
                )
            )
        else:
            items.append(
                '<li><a href="{url}"><img src="{url}" alt="{label}" loading="lazy">'
                '<span>{label}</span></a></li>'.format(
                    url=html.escape(url, quote=True), label=label,
                )
            )
    gallery = '<ul class="gallery">' + ''.join(items) + '</ul>' if items else '<p class="empty">No files yet.</p>'
    notice = f'<p class="notice">{html.escape(message)}</p>' if message else ''
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shared User Space</title>
<style>
body {{ font: 16px system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #202124; }}
h1 {{ margin-bottom: .25rem; }}
.hint {{ color: #5f6368; }}
form {{ border: 1px solid #dadce0; border-radius: 8px; padding: 1rem; margin: 1.5rem 0; }}
input[type=file] {{ max-width: 100%; }}
button {{ margin-top: .75rem; padding: .5rem 1rem; }}
.notice {{ background: #e8f0fe; padding: .75rem; border-radius: 6px; }}
.gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr)); gap: 1rem; list-style: none; padding: 0; }}
.gallery a {{ display: block; color: inherit; text-decoration: none; }}
.gallery img {{ width: 100%; height: 10rem; object-fit: cover; border-radius: 6px; background: #f1f3f4; }}
.gallery span {{ display: block; overflow-wrap: anywhere; margin-top: .35rem; }}
.gallery .file {{ border: 1px solid #dadce0; border-radius: 6px; padding: .75rem; }}
.gallery .type {{ font-size: .75rem; color: #5f6368; }}
.gallery small {{ display: block; color: #5f6368; margin-top: .25rem; }}
.empty {{ color: #5f6368; }}
</style>
</head>
<body>
<h1>Shared User Space</h1>
<p class="hint">Images: JPG, JPEG, PNG, WebP (25 MiB each). Text: TXT (1 MiB each). Up to 10 files per upload.</p>
<p class="hint">For lists, open a terminal and run <code>nano "$HOME/Shared User Space/lists.txt"</code>.</p>
{notice}
<form method="post" action="/shared/upload" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{token}">
<input type="file" name="files" accept=".jpg,.jpeg,.png,.webp,.txt,image/jpeg,image/png,image/webp,text/plain" multiple required>
<button type="submit">Add files</button>
</form>
{gallery}
</body>
</html>
"""
    return page.encode('utf-8'), status


class Handler(BaseHTTPRequestHandler):
    server_version = 'SharedUserSpace/1.0'

    def _send_bytes(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_page(self, message: str = '', status: int = 200) -> None:
        body, status = render_page(self.server.root, self.server.csrf_token, message, status)
        self._send_bytes(status, body, 'text/html; charset=utf-8')

    def _send_error_page(self, status: int, message: str) -> None:
        self._send_page(message, status)

    def _same_origin(self) -> bool:
        origin = self.headers.get('Origin')
        if not origin:
            return True
        return urlsplit(origin).netloc == self.headers.get('Host', '')

    def _serve_file(self, raw_name: str) -> None:
        name = safe_filename(unquote(raw_name))
        if not name:
            self._send_error_page(HTTPStatus.NOT_FOUND, 'File not found.')
            return
        path = self.server.root / name
        suffix = path.suffix.lower()
        limit = MAX_TEXT_BYTES if suffix in TEXT_EXTENSIONS else MAX_IMAGE_BYTES
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except OSError:
            self._send_error_page(HTTPStatus.NOT_FOUND, 'File not found.')
            return
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                self._send_error_page(HTTPStatus.NOT_FOUND, 'File not found.')
                return
            with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                data = handle.read(limit + 1)
        finally:
            os.close(descriptor)
        if len(data) > limit or not file_kind(name, data):
            self._send_error_page(HTTPStatus.NOT_FOUND, 'File not found.')
            return
        self._send_bytes(
            HTTPStatus.OK,
            data,
            MIME_TYPES[suffix],
            {'Content-Disposition': f'inline; filename="{name}"'},
        )

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in ('/shared', '/shared/'):
            uploaded = parse_qs(urlsplit(self.path).query).get('uploaded', [''])[0]
            message = f'{uploaded} file(s) added.' if uploaded.isdigit() else ''
            self._send_page(message)
        elif path.startswith('/shared/files/'):
            self._serve_file(path[len('/shared/files/'):])
        else:
            self._send_error_page(HTTPStatus.NOT_FOUND, 'Not found.')

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        if urlsplit(self.path).path != '/shared/upload':
            self._send_error_page(HTTPStatus.NOT_FOUND, 'Not found.')
            return
        if not self._same_origin():
            self._send_error_page(HTTPStatus.FORBIDDEN, 'Cross-origin upload refused.')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_error_page(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Upload is empty or too large.')
            return
        body = self.rfile.read(length)
        if len(body) != length:
            self._send_error_page(HTTPStatus.BAD_REQUEST, 'Incomplete upload.')
            return
        try:
            token, files = parse_multipart(self.headers.get('Content-Type', ''), body)
        except (ValueError, email.errors.MessageError):
            self._send_error_page(HTTPStatus.BAD_REQUEST, 'Malformed upload.')
            return
        if not token or not hmac.compare_digest(token, self.server.csrf_token):
            self._send_error_page(HTTPStatus.FORBIDDEN, 'Invalid form token.')
            return
        if not files:
            self._send_error_page(HTTPStatus.BAD_REQUEST, 'Choose at least one file.')
            return
        if len(files) > MAX_FILES:
            self._send_error_page(HTTPStatus.BAD_REQUEST, f'At most {MAX_FILES} files per upload.')
            return
        saved = []
        errors = []
        total = 0
        for filename, data in files:
            total += len(data)
            if total > MAX_REQUEST_BYTES:
                errors.append('Total upload is too large.')
                break
            name = safe_filename(filename)
            if not name:
                errors.append(f'{filename}: unsupported filename or extension.')
                continue
            suffix = pathlib.PurePath(name).suffix.lower()
            limit = MAX_TEXT_BYTES if suffix in TEXT_EXTENSIONS else MAX_IMAGE_BYTES
            if len(data) > limit:
                errors.append(f'{filename}: larger than the per-file limit.')
                continue
            if not file_kind(name, data):
                errors.append(f'{filename}: not a matching JPEG, PNG, WebP, or UTF-8 text file.')
                continue
            try:
                saved.append(store_file(self.server.root, name, data).name)
            except (OSError, RuntimeError) as exc:
                print(f'shared-space: could not store an upload: {exc}', file=sys.stderr)
                errors.append(f'{filename}: could not be stored.')
        if saved and not errors:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header('Location', f'/shared/?uploaded={len(saved)}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        message = ''
        if saved:
            message += f'{len(saved)} file(s) added. '
        if errors:
            message += ' '.join(errors)
        self._send_page(message, HTTPStatus.OK if saved else HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write('%s - - [%s] %s\n' % (
            self.address_string(), self.log_date_time_string(), fmt % args,
        ))


def patch_caddy(path: pathlib.Path) -> bool:
    """Insert the /shared route if absent. Returns True when changed."""
    path = pathlib.Path(path)
    source = path.read_text(encoding='utf-8')
    if re.search(r'(?m)^\s*@shared\b', source):
        return False
    match = re.search(r'(?m)^([ \t]*)reverse_proxy http://127\.0\.0\.1:6080\b', source)
    if not match:
        raise ValueError('Caddyfile has no 127.0.0.1:6080 reverse_proxy')
    indent = match.group(1)
    route = (
        f'{indent}@shared path /shared /shared/*\n'
        f'{indent}handle @shared {{\n'
        f'{indent}    request_body {{\n'
        f'{indent}        max_size 100MB\n'
        f'{indent}    }}\n'
        f'{indent}    reverse_proxy http://127.0.0.1:8082\n'
        f'{indent}}}\n'
    )
    path.write_text(source[:match.start()] + route + source[match.start():], encoding='utf-8')
    return True


def serve(root: pathlib.Path, bind: str, port: int) -> int:
    root = ensure_space(root)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    server.root = root.resolve()
    server.csrf_token = secrets.token_urlsafe(32)
    print(f'Shared User Space listening on {bind}:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Shared User Space')
    commands = parser.add_subparsers(dest='command', required=True)
    serve_parser = commands.add_parser('serve', help='run the upload service')
    serve_parser.add_argument('--root', type=pathlib.Path, required=True)
    serve_parser.add_argument('--port', type=int, default=8082)
    serve_parser.add_argument('--bind', default='127.0.0.1')
    ensure_parser = commands.add_parser('ensure', help='create or repair storage')
    ensure_parser.add_argument('--root', type=pathlib.Path, required=True)
    patch_parser = commands.add_parser('patch-caddy', help='add the /shared route')
    patch_parser.add_argument('file', type=pathlib.Path)
    args = parser.parse_args()
    if args.command == 'serve':
        return serve(args.root, args.bind, args.port)
    if args.command == 'ensure':
        print(ensure_space(args.root))
        return 0
    try:
        changed = patch_caddy(args.file)
    except (OSError, ValueError) as exc:
        print(f'shared-space: {exc}', file=sys.stderr)
        return 1
    print('patched' if changed else 'already present')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
