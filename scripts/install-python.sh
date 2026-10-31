#!/usr/bin/env bash
# Install upstream stable Python alongside Ubuntu's system Python, then provide a per-user venv.
# This script is called as a normal user after bootstrap-host.sh installs compiler dependencies.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export LXQT_PROJECT_ROOT="$ROOT"
source "$ROOT/scripts/cpu-affinity.sh"
lxqt_cpu_guard "$(lxqt_cpu_set)"
[[ "$(id -u)" != 0 ]] || { echo 'Run as the normal user, NOT with sudo.' >&2; exit 1; }
PYTHON_VERSION="${LXQT_PYTHON_VERSION:-3.15.2}"
PYTHON_SHA256="${LXQT_PYTHON_SHA256:-8b5536d280142ecf6cdfe15d12a21ba1c65e300bbf7b28a8d0cdb849575797de}"
PYTHON_MINOR="${PYTHON_VERSION%.*}"
PYYAML_VERSION="${LXQT_PYYAML_VERSION:-6.0.3}"
[[ "$PYYAML_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid PyYAML version' >&2; exit 1; }
PREFIX="/opt/lxqt-kind-python/${PYTHON_VERSION}"
VENV="${HOME}/.local/share/lxqt-kind-python"
LINK="${HOME}/.local/bin/python"
JOBS="${LXQT_PYTHON_BUILD_JOBS:-2}"
[[ "$PYTHON_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid Python version' >&2; exit 1; }
[[ "$PYTHON_SHA256" =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid Python SHA256 value' >&2; exit 1; }
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid build jobs' >&2; exit 1; }
for exe in curl sha256sum tar make gcc sudo; do command -v "$exe" >/dev/null || { echo "Missing $exe" >&2; exit 1; }; done
python_binary="$PREFIX/bin/python${PYTHON_MINOR}"
installed_python_version="$("$python_binary" -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || true)"
if [[ "$installed_python_version" != "$PYTHON_VERSION" ]]; then
    if [[ -e "$PREFIX" ]]; then
        backup_prefix="${PREFIX}.lxqt-backup-$(date +%Y%m%d%H%M%S)-$$"
        echo "Installed Python is missing, broken or unexpected (${installed_python_version:-unavailable}). Preserving previous prefix at $backup_prefix"
        sudo mv "$PREFIX" "$backup_prefix"
    fi
    # A privileged `make altinstall` can create root-owned __pycache__ files in
    # the source tree (ensurepip uses Python -E and ignores bytecode env flags).
    # Safely scope privileged recovery to our own randomly-created work directory.
    WORK="$(mktemp -d "${TMPDIR:-/tmp}/lxqt-python-build.XXXXXXXX")"
    cleanup_python_build() {
        local original_status=$?
        trap - EXIT
        if [[ -n "${WORK:-}" && -d "$WORK" ]]; then
            if ! rm -rf -- "$WORK" 2>/dev/null; then
                echo "Cleaning root-owned Python build artifacts in $WORK ..." >&2
                if [[ "$WORK" == "${TMPDIR:-/tmp}"/lxqt-python-build.* ]] && sudo -n chown -R -- "$(id -u):$(id -g)" "$WORK"; then
                    rm -rf -- "$WORK" || echo "Warning: residual build files in $WORK" >&2
                else
                    echo "Warning: build cleanup needs privileges; inspect and remove $WORK manually." >&2
                fi
            fi
        fi
        return "$original_status"
    }
    trap cleanup_python_build EXIT
    SOURCE="Python-${PYTHON_VERSION}.tar.xz"
    echo "Fetching Python ${PYTHON_VERSION} from python.org and verifying SHA-256 ..."
    curl --proto '=https' --tlsv1.2 -fL --retry 3 -o "$WORK/$SOURCE" "https://www.python.org/ftp/python/${PYTHON_VERSION}/${SOURCE}"
    printf '%s  %s\n' "$PYTHON_SHA256" "$WORK/$SOURCE" | sha256sum -c -
    tar -xJf "$WORK/$SOURCE" -C "$WORK"
    pushd "$WORK/Python-${PYTHON_VERSION}" >/dev/null
    ./configure --prefix="$PREFIX" --with-ensurepip=install
    if [[ -n "${LXQT_PYTHON_BUILD_CPUSET:-}" ]]; then
      command -v taskset >/dev/null || { echo 'taskset required for LXQT_PYTHON_BUILD_CPUSET' >&2; exit 1; }
      taskset -c "$LXQT_PYTHON_BUILD_CPUSET" make -j "$JOBS"
    else
      make -j "$JOBS"
    fi
    sudo make altinstall
    popd >/dev/null
fi
"$PREFIX/bin/python${PYTHON_MINOR}" --version
# A venv installed in the user's home makes 'python -c "import yaml"' work without touching the OS Python.
if [[ -e "$VENV/pyvenv.cfg" ]]; then
    if ! "$VENV/bin/python" -c "import sys; assert sys.version_info[:3] == tuple(map(int, '${PYTHON_VERSION}'.split('.')))" 2>/dev/null; then
        backup="${VENV}.lxqt-backup-$(date +%Y%m%d%H%M%S)-$$"
        echo "Python environment version changed or is damaged. Preserving it at $backup"
        mv "$VENV" "$backup"
        "$PREFIX/bin/python${PYTHON_MINOR}" -m venv "$VENV"
    fi
else
    if [[ -d "$VENV" ]]; then
        backup="${VENV}.lxqt-backup-$(date +%Y%m%d%H%M%S)-$$"
        echo "Preserving incomplete environment at $backup"
        mv "$VENV" "$backup"
    fi
    "$PREFIX/bin/python${PYTHON_MINOR}" -m venv "$VENV"
fi
# Only reinstall Python packages when missing, mismatched, or broken.
current_yaml="$("$VENV/bin/python" -c 'import yaml; print(yaml.__version__)' 2>/dev/null || true)"
if [[ "$current_yaml" != "$PYYAML_VERSION" ]]; then
    echo "PyYAML change detected: ${current_yaml:-missing/broken} -> $PYYAML_VERSION"
    "$VENV/bin/python" -m pip install --upgrade pip
    # Force a pure-Python build when PyYAML wheels are not available for this interpreter.
    PYYAML_FORCE_LIBYAML=0 "$VENV/bin/python" -m pip install --no-binary=PyYAML "PyYAML==$PYYAML_VERSION"
else
    echo "PyYAML $PYYAML_VERSION already present; no reinstall necessary."
fi
"$VENV/bin/python" -c 'import yaml,sys; print("Python:",sys.version.split()[0],"PyYAML:",yaml.__version__)'
mkdir -p "$HOME/.local/bin"
if [[ -e "$LINK" || -L "$LINK" ]]; then
    if [[ ! -L "$LINK" || "$(readlink "$LINK")" != "$VENV/bin/python" ]]; then
        echo "Refusing to overwrite an unrelated existing python command at $LINK. Rename it first." >&2
        exit 1
    fi
fi
ln -sfn "$VENV/bin/python" "$LINK"
# Deliberately do not alter /usr/bin/python, /usr/bin/python3 or the system python3 alternatives.
for profile in "$HOME/.profile" "$HOME/.bashrc"; do
    touch "$profile"
    if ! grep -Fqx '# >>> lxqt-kind-python >>>' "$profile"; then
        cat >> "$profile" <<'PROFILE'

# >>> lxqt-kind-python >>>
# Project-local python alias and executable; does not replace Ubuntu's python3.
export PATH="$HOME/.local/bin:$PATH"
alias python="$HOME/.local/share/lxqt-kind-python/bin/python"
# <<< lxqt-kind-python <<<
PROFILE
    fi
done
if [[ -f "$HOME/.zshrc" ]]; then
    if ! grep -Fqx '# >>> lxqt-kind-python >>>' "$HOME/.zshrc"; then
        cat >> "$HOME/.zshrc" <<'PROFILE'

# >>> lxqt-kind-python >>>
export PATH="$HOME/.local/bin:$PATH"
alias python="$HOME/.local/share/lxqt-kind-python/bin/python"
# <<< lxqt-kind-python <<<
PROFILE
    fi
fi
printf '\nPython installation complete. Open a new shell or run: source ~/.bashrc\n'
printf 'Verify: python --version && python -c "import yaml; print(yaml.__version__)"\n'
printf 'System python3 remains unchanged.\n'
