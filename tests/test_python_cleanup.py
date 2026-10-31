"""Regression tests for sudo-created bytecode in a Python source build."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'scripts/install-python.sh').read_text()

class PythonBuildCleanupTests(unittest.TestCase):
    def test_no_unbounded_privileged_deletion(self):
        self.assertIn('cleanup_python_build()', SCRIPT)
        self.assertIn('trap cleanup_python_build EXIT', SCRIPT)
        self.assertIn('sudo -n chown -R --', SCRIPT)
        self.assertIn('return "$original_status"', SCRIPT)
        self.assertNotIn('sudo rm -rf', SCRIPT)
        self.assertNotIn("trap 'rm -rf \"$WORK\"' EXIT", SCRIPT)
        self.assertIn('"${TMPDIR:-/tmp}/lxqt-python-build.XXXXXXXX"', SCRIPT)

    def test_cleanup_uses_fallback_when_first_rm_fails(self):
        # Isolate the cleanup function: no Python compilation and no sudo calls.
        start = SCRIPT.index('    cleanup_python_build() {')
        end = SCRIPT.index('    trap cleanup_python_build EXIT', start)
        function = SCRIPT[start:end]
        with tempfile.TemporaryDirectory() as td:
            work=Path(td)/'lxqt-python-build.12345678'
            work.mkdir()
            script=f"""set -euo pipefail
                WORK='{work}'
                TMPDIR='{td}'
                {function}
                COUNT=0
                rm() {{ COUNT=$((COUNT+1)); if [[ $COUNT -eq 1 ]]; then return 1; fi; command rm "$@"; }}
                sudo() {{ [[ "$1" == '-n' && "$2" == 'chown' ]] || return 9; return 0; }}
                cleanup_python_build
                [[ ! -e '{work}' ]]
            """
            proc=subprocess.run(['bash','-c',script],text=True,capture_output=True)
            self.assertEqual(proc.returncode,0, proc.stderr)

if __name__ == '__main__':
    unittest.main()
