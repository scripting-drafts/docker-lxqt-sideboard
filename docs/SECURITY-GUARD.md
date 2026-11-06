# Explicit-authorization guard

The LXQt shared-browser stack must not start unless the operator explicitly
authorizes it. This is enforced in two layers:

1. `scripts/deploy.sh` creates an authorization marker because a deploy is an
   explicit operator action.
2. A guard service watches Docker/Kubernetes every five seconds. If Kind,
   BuildKit, or the `lxqt-desktop` Deployment starts without the marker, it
   stops the containers and scales the Deployment back to zero.

## Install

As the normal Docker-authorized user:

```bash
./scripts/install-guard.sh
```

This installs:

- `~/.local/bin/lxqt-stack` (project wrapper)
- `~/.config/systemd/user/lxqt-stack-guard.service`
- user lingering, so the guard also runs while logged out

If the stack was already running, add `--authorize-current`; otherwise the
guard will stop it because no marker exists.

For a boot-level unit owned by the operator instead of a user unit:

```bash
sudo ./scripts/install-guard.sh --system
```

The system template is `systemd/lxqt-stack-guard-system.service.in`. It runs
as the operator, not root, but starts at boot without user lingering.

Remove either unit with:

```bash
./scripts/install-guard.sh --uninstall
sudo ./scripts/install-guard.sh --system --uninstall
```

## Use

```bash
lxqt-stack status
lxqt-stack start          # interactive: type START
lxqt-stack start --yes
lxqt-stack authorize "manual maintenance"
lxqt-stack stop           # revoke authorization and stop the stack
```

Normal state after installation is stopped, `restart=no`, Deployment replicas
0, and no authorization marker.

## Verification

```bash
systemctl --user status lxqt-stack-guard.service
journalctl --user -u lxqt-stack-guard.service -n 20
lxqt-stack status
```

An unauthorized `docker start lxqt-secure-control-plane` or
`docker start buildx_buildkit_lxqt-cpu-lxqt-secure0` is detected and stopped.

## Limits

The guard is an authorization control, not a security boundary against the
host owner. The operator can stop the guard, and root or anyone in the Docker
group can bypass it. A strict multi-user boundary requires host policy,
root-level systemd enforcement, or a container authorization plugin.
