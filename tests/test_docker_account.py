"""Offline regression checks for Ubuntu 24.04's occupied UID/GID 1000."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def account_shell():
    dockerfile = (ROOT / 'Dockerfile').read_text()
    account = dockerfile.split('RUN set -eux; \\\n', 1)[1].split('\nCOPY --chmod=', 1)[0]
    # Rejoin Dockerfile RUN shell continuations into executable source.
    return 'set -eux; ' + account.replace('\\\n', '\n')


class AccountTests(unittest.TestCase):
    def _run(self, existing):
        shell = account_shell()
        with tempfile.TemporaryDirectory() as folder:
            binpath = Path(folder) / 'bin'
            binpath.mkdir()
            log = Path(folder) / 'calls.log'
            (binpath / 'getent').write_text(
                '#!/bin/sh\n'
                'if [ "$2" = 1000 ] && [ "$1" = group ]; then\n'
                '  [ "$EXISTING_ACCOUNT" = yes ] || exit 2\n'
                '  echo ubuntu:x:1000:\n'
                'elif [ "$2" = 1000 ] && [ "$1" = passwd ]; then\n'
                '  [ "$EXISTING_ACCOUNT" = yes ] || exit 2\n'
                '  echo ubuntu:x:1000:1000:Ubuntu:/home/ubuntu:/bin/bash\n'
                'else exit 2; fi\n'
            )
            for cmd in ('groupmod', 'usermod', 'groupadd', 'useradd', 'mkdir', 'chown'):
                (binpath / cmd).write_text('#!/bin/sh\necho "' + cmd + ' $*" >> "$ACTION_LOG"\n')
            (binpath / 'id').write_text('#!/bin/sh\nprintf "1000\\n"\n')
            for file in binpath.iterdir():
                file.chmod(0o755)
            env = {**os.environ, 'PATH': f'{binpath}:{os.environ["PATH"]}',
                   'EXISTING_ACCOUNT': 'yes' if existing else 'no', 'ACTION_LOG': str(log)}
            result = subprocess.run(['bash', '-c', shell], cwd=folder, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return log.read_text()

    def test_ubuntu_2404_account_is_renamed_not_duplicated(self):
        calls = self._run(True)
        self.assertIn('groupmod --new-name desktop ubuntu', calls)
        self.assertIn('usermod --login desktop --home /home/desktop --move-home --shell /bin/bash ubuntu', calls)
        self.assertNotIn('groupadd ', calls)
        self.assertNotIn('useradd ', calls)
        self.assertIn('chown -R 1000:1000 /home/desktop', calls)

    def test_free_uid_gid_creates_desktop_identity(self):
        calls = self._run(False)
        self.assertIn('groupadd --gid 1000 desktop', calls)
        self.assertIn('useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash desktop', calls)
        self.assertNotIn('groupmod ', calls)
        self.assertNotIn('usermod ', calls)

    def test_package_and_user_provisioning_are_separate_steps(self):
        source = (ROOT / 'Dockerfile').read_text()
        self.assertIn('FROM ubuntu:24.04', source)
        self.assertIn('RUN set -eux;', source)
        self.assertLess(source.index('apt-get install'), source.index('RUN set -eux;'))
        subprocess.run(['bash', '-n', '-c', account_shell()], check=True)


if __name__ == '__main__':
    unittest.main()
