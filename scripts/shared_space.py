#!/usr/bin/env python3
"""Image-only upload endpoint for the Shared User Space."""
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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
MIME_TYPES = {
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 100 * 1024 * 1024
MAX_FILES = 10
NAME_RE = re.compile(
    r'^[A-Za-z0-9][A-Za-z0-9 ._-]{0,119}\.(?:jpe?g|png|webp)$',
    re.IGNORECASE,
)
CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def safe_filename(raw: str | None) -> str | None:
    """Return a flat, image-named filename or None."""
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


def extension_matches(name: str, kind: str) -> bool:
    suffix = pathlib.PurePath(name).suffix.lower()
    if kind == '.jpg':
        return suffix in ('.jpg', '.jpeg')
    return suffix == kind


def store_image(root: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
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
            if filename and payload:
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
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        name = path.name
        url = '/shared/files/' + quote(name, safe='')
        items.append(
            '<li><a href="{url}"><img src="{url}" alt="{label}" loading="lazy">'
            '<span>{label}</span></a></li>'.format(
                url=html.escape(url, quote=True),
                label=html.escape(name),
            )
        )
    gallery = '<ul class="gallery">' + ''.join(items) + '</ul>' if items else '<p class="empty">No images yet.</p>'
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
.empty {{ color: #5f6368; }}
</style>
</head>
<body>
<h1>Shared User Space</h1>
<p class="hint">Images only: JPG, JPEG, PNG, WebP. Maximum 25 MiB per file, 10 files per upload.</p>
<p class="hint">For text lists, open a terminal and run <code>nano "$HOME/Shared User Space/lists.txt"</code>.</p>
{notice}
<form method="post" action="/shared/upload" enctype="multipart/form-data">
<input type="hidden" name="csrf" value="{token}">
<input type="file" name="files" accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp" multiple required>
<button type="submit">Add images</button>
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
            self._send_error_page(HTTPStatus.NOT_FOUND, 'Image not found.')
            return
        path = self.server.root / name
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        except OSError:
            self._send_error_page(HTTPStatus.NOT_FOUND, 'Image not found.')
            return
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                self._send_error_page(HTTPStatus.NOT_FOUND, 'Image not found.')
                return
            with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                data = handle.read(MAX_FILE_BYTES + 1)
        finally:
            os.close(descriptor)
        kind = image_kind(data)
        if len(data) > MAX_FILE_BYTES or not kind or not extension_matches(name, kind):
            self._send_error_page(HTTPStatus.NOT_FOUND, 'Image not found.')
            return
        content_type = MIME_TYPES[path.suffix.lower()]
        self._send_bytes(
            HTTPStatus.OK,
            data,
            content_type,
            {'Content-Disposition': f'inline; filename="{name}"'},
        )

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in ('/shared', '/shared/'):
            uploaded = parse_qs(urlsplit(self.path).query).get('uploaded', [''])[0]
            message = f'{uploaded} image(s) added.' if uploaded.isdigit() else ''
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
            self._send_error_page(HTTPStatus.BAD_REQUEST, 'Choose at least one image.')
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
            kind = image_kind(data)
            if not kind or not extension_matches(name, kind):
                errors.append(f'{filename}: not a matching JPEG, PNG, or WebP image.')
                continue
            if len(data) > MAX_FILE_BYTES:
                errors.append(f'{filename}: larger than 25 MiB.')
                continue
            try:
                saved.append(store_image(self.server.root, name, data).name)
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
            message += f'{len(saved)} image(s) added. '
        if errors:
            message += ' '.join(errors)
        self._send_page(message, HTTPStatus.OK if saved else HTTPStatus.BAD_REQUEST)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write('%s - - [%s] %s\n' % (
            self.address_string(), self.log_date_time_string(), fmt % args,
        ))


def main() -> int:
    parser = argparse.ArgumentParser(description='Shared User Space image uploads')
    parser.add_argument('--root', type=pathlib.Path, required=True)
    parser.add_argument('--port', type=int, default=8082)
    parser.add_argument('--bind', default='127.0.0.1')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    os.chmod(args.root, 0o755)
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.daemon_threads = True
    server.root = args.root.resolve()
    server.csrf_token = secrets.token_urlsafe(32)
    print(f'Shared User Space listening on {args.bind}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
