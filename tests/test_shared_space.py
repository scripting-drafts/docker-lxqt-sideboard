"""Minimal checks for the Shared User Space upload and repair path."""
import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shared_space', ROOT / 'scripts/shared_space.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)

CADDY = '''http://:8081 {
    bind 127.0.0.1
    basic_auth argon2id {
        viewer synthetic
    }
    reverse_proxy http://127.0.0.1:6080 {
        stream_close_delay 5m
    }
}
'''


class SharedSpaceTests(unittest.TestCase):
    def test_safe_filenames(self):
        for name in ('photo.JPG', 'my photo.webp', 'notes.txt', 'a.jpeg'):
            self.assertIsNotNone(shared.safe_filename(name))
        for name in ('.hidden.jpg', 'x.gif', 'x.jpg.html', 'x.md', 'x', ''):
            self.assertIsNone(shared.safe_filename(name))
        self.assertEqual(shared.safe_filename('../x.jpg'), 'x.jpg')

    def test_image_and_text_validation(self):
        self.assertEqual(shared.image_kind(b'\xff\xd8\xffdata'), '.jpg')
        self.assertEqual(shared.image_kind(b'\x89PNG\r\n\x1a\ndata'), '.png')
        self.assertEqual(shared.image_kind(b'RIFF1234WEBPdata'), '.webp')
        self.assertIsNone(shared.image_kind(b'GIF89adata'))
        self.assertEqual(shared.file_kind('notes.txt', 'hello\n'.encode()), '.txt')
        self.assertIsNone(shared.file_kind('notes.txt', b'\xff\x00bad'))
        self.assertEqual(shared.file_kind('notes.png', b'\x89PNG\r\n\x1a\n'), '.png')
        self.assertIsNone(shared.file_kind('notes.png', b'\xff\xd8\xffdata'))

    def test_store_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            first = shared.store_file(root, 'photo.jpg', b'\xff\xd8\xffone')
            second = shared.store_file(root, 'photo.jpg', b'\xff\xd8\xfftwo')
            self.assertEqual(first.name, 'photo.jpg')
            self.assertEqual(second.name, 'photo-1.jpg')
            self.assertEqual(first.read_bytes(), b'\xff\xd8\xffone')

    def test_ensure_removes_empty_lists_dir(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / 'lists').mkdir()
            shared.ensure_space(root)
            self.assertFalse((root / 'lists').exists())
            self.assertTrue((root / 'README.txt').is_file())

    def test_patch_caddy_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'Caddyfile'
            path.write_text(CADDY)
            self.assertTrue(shared.patch_caddy(path))
            once = path.read_text()
            self.assertIn('redir / /vnc.html 302', once)
            self.assertIn('@shared path /shared /shared/*', once)
            self.assertIn('reverse_proxy http://127.0.0.1:8082', once)
            self.assertFalse(shared.patch_caddy(path))
            self.assertEqual(path.read_text(), once)

    def test_multipart_parser_returns_token_and_file(self):
        boundary = '----shared-space-test'
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\ntoken\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="photo.jpg"\r\n'
            'Content-Type: image/jpeg\r\n\r\n'
        ).encode() + b'\xff\xd8\xffdata' + f'\r\n--{boundary}--\r\n'.encode()
        token, files = shared.parse_multipart(
            f'multipart/form-data; boundary={boundary}', body,
        )
        self.assertEqual(token, 'token')
        self.assertEqual(files[0][0], 'photo.jpg')
        self.assertTrue(files[0][1].startswith(b'\xff\xd8\xff'))

    def test_build_and_route_wiring(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        overlay = (ROOT / 'Dockerfile.overlay').read_text()
        start = (ROOT / 'start-desktop.sh').read_text()
        configure = (ROOT / 'scripts/configure.py').read_text()
        fix_caddy = (ROOT / 'scripts/fix-caddy.sh').read_text()
        script = (ROOT / 'scripts/shared_space.py').read_text()
        ignore = (ROOT / '.dockerignore').read_text()
        self.assertIn('nano', dockerfile)
        self.assertIn('assets/novnc-index.html /usr/share/novnc/index.html', dockerfile)
        self.assertIn('scripts/shared_space.py /usr/local/bin/shared-space', dockerfile)
        self.assertIn('assets/novnc-index.html /usr/share/novnc/index.html', overlay)
        self.assertIn('scripts/shared_space.py /usr/local/bin/shared-space', overlay)
        self.assertIn('shared-space serve', start)
        self.assertIn('patch-caddy', configure)
        self.assertIn('patch-caddy', fix_caddy)
        self.assertIn('@shared path /shared /shared/*', script)
        self.assertIn('!scripts/shared_space.py', ignore)


if __name__ == '__main__':
    unittest.main()
