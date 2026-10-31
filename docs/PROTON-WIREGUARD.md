# Proton VPN Free / WireGuard — installation and migration to Kind

This guide combines Proton's manual WireGuard instructions with the project's Gluetun VPN-in-Pod design. Host-side split routing is outside this project.

## What is automated, and what must be done by a person?

| Action | Responsible component |
|---|---|
| Proton account, download a WireGuard `.conf` | **You, in Proton's official account portal**; no unattended login/scraping |
| Host `wireguard-tools` (`wg`/`wg-quick`) + `iproute2` | `bootstrap-host.sh` or `setup-proton.sh` |
| TUN device checks | `verify-tun.sh` (included in setup-proton unless skipped) |
| Validate Proton `.conf`, import Gluetun secret | `setup-proton.sh` / `proton-wireguard.py` |
| Detect same known active host VPN profile | `proton-wireguard.py check-host` (called by deploy.sh) |
| Run the VPN and firewall for the desktop Pod | Gluetun restartable init-sidecar |
| Choose free-tier servers | `FREE_ONLY=on` (default) and optional `SERVER_COUNTRIES` |
| Validate Pod VPN health and show egress IP | `verify-proton.sh` |
| Import / activate host NetworkManager VPN | **Opt-in only**: manual `nmcli` or `proton-wireguard.py import-host` |

The **Proton VPN native GUI/CLI package is intentionally not installed**: Gluetun speaks WireGuard directly and implements the desktop Pod's VPN. Installing Proton's GUI on the host would not help isolate the Pod and could change routes that the host relies on.

## 1. Get a WireGuard configuration from Proton

Sign in to [Proton VPN's account configuration page](https://account.proton.me/u/0/vpn/WireGuard) and create/download a configuration for a Free-plan server. Download the full `.conf` file. The key belongs to your Proton account; do not commit it to Git, paste it into chat, or share it in logs.

NetworkManager requires a short valid WireGuard interface name (maximum 15 bytes). The following names are examples, not real credentials or profiles.

For host NetworkManager:

```bash
mv ~/Documents/downloaded-wireguard.conf ~/Documents/wg-example.conf
chmod 600 ~/Documents/wg-example.conf
nmcli conn import type wireguard file ~/Documents/wg-example.conf
nmcli connection up wg-example
nmcli connection show --active
ip -br addr show wg-example
sudo wg show wg-example
```

These are illustrative host commands, *not* a prerequisite for the Pod. `nmcli` import can connect automatically. Do **not** run these on a remote SSH-only host without checking route continuity. If the profile already exists, do not reimport it. `sudo wg show` requires privileges to inspect the interface and should show a current handshake and increasing TX/RX counts. `ping google.com` only establishes reachability; it does not establish which public IP is used.

`wg: command not found` is solved by `sudo apt install wireguard-tools`. `wg show ...` returning `Operation not permitted` as an unprivileged user is normally fixed by running `sudo wg show ...`.

## 2. Prepare the Proton key for Kubernetes (recommended)

The installer runs with **no required arguments**: `./install.sh`. It checks `~/Documents/wireguard.conf` first and **prompts for a `.conf` path if that file is missing**, before changing the host. You can also provide `--wireguard-config /path/to/file.conf` (or a positional path) as an optional override. You do not need to rename your downloaded file. The `.conf` filename is *not* necessarily the active NetworkManager connection name. For an existing host connection named `wg-example` using the same key, use a separate Proton profile for Kubernetes or pass `--host-profile wg-example` for the name-based conflict guard. The installer does not automatically disconnect a host VPN.


```bash
./scripts/bootstrap-host.sh --dry-run
./scripts/bootstrap-host.sh --add-docker-group    # Optional: root-equivalent Docker access
# Log out and in if Docker group membership was changed.
./scripts/setup-proton.sh \
  --config "$HOME/Documents/wireguard.conf" \
  --host-profile wg-example  # only if the matching host connection is named wg-example
```

`setup-proton.sh` installs `wireguard-tools` if it is missing, verifies the TUN device inside a disposable Docker container, parses the configuration and saves only the **private key** as `config/secrets/wireguard_private_key` with mode `0600`. It records nonsecret source metadata in `config/proton-source.json` with mode `0600`. Source files are **not copied into Kubernetes** and are not printed. Importing a source file **does not** activate, deactivate or change your host's existing VPN.

Alternatively, do the import during the interactive configuration wizard:

```bash
python scripts/configure.py \
  --host-profile wg-example  # only if the matching host connection is named wg-example
```

The rest of the wizard prompts for Tailscale/Caddy parameters and persists your other settings. Enter to retain the already imported Proton key if prompted. You can also paste a private key through an invisible prompt, without a source `.conf` file; then the importer cannot associate it with a known host connection and duplicate-key safety checks are less complete.

### How Gluetun consumes the configuration

The deployment loads the imported key via a Kubernetes Secret **file mount** and the `WIREGUARD_PRIVATE_KEY_SECRETFILE` environment variable. Relevant nonsecret environment entries are:

```yaml
VPN_SERVICE_PROVIDER: protonvpn
VPN_TYPE: wireguard
FREE_ONLY: on
VPN_PORT_FORWARDING: off
WIREGUARD_PRIVATE_KEY_SECRETFILE: /run/secrets/wireguard_private_key
```

Gluetun selects a Proton server from its own provider catalog; it does not automatically use the host tunnel's endpoint. To choose a country, set `proton_server_countries` in `config/settings.json` through the interactive wizard. Availability is subject to provider and account restrictions.

## 3. Before you deploy, avoid duplicate active WireGuard identities

Using the same WireGuard private key from both the host and Pod may cause peer endpoint roaming/flapping.

The `deploy.sh` script invokes:

```bash
python3 scripts/proton-wireguard.py check-host
```

If the known matching NetworkManager profile is active, deployment **fails closed** with instructions. The script **never disconnects** the host for you. Two safe paths:

1. Prefer generating a **separate Proton WireGuard configuration** for the Pod (different private key) if the host VPN should remain connected. Run `setup-proton.sh` again with the new file and its distinct host-profile name.
2. Otherwise, after ensuring you have local/out-of-band network access, consciously disconnect your existing host tunnel: `nmcli connection down wg-example`. This can interrupt SSH/network access. Then retry `./scripts/deploy.sh`.

The guard can only match a known NetworkManager profile. It cannot detect every reuse of a WireGuard key under a different name, via wg-quick, or on another machine. **Never assume successful preflight guarantees exclusive use of a key.**

## 4. Deploy and verify actual network egress

```bash
python3 scripts/configure.py
./scripts/deploy.sh
./scripts/verify-proton.sh
./scripts/enable-funnel.sh
```

`verify-proton.sh` checks the Gluetun health endpoint and prints the Pod's externally observed public IP. It also shows the host's public IP if available. The tunnel can be healthy even if a firewall configuration is wrong. **An egress-IP comparison alone does not prove leak-proof VPN enforcement**; test a controlled VPN outage and verify the desktop cannot reach the internet before trusting the kill switch with sensitive workloads. Keep the shared public desktop private until checks are complete.

Verify the current handshake and transfer counters locally without publishing addresses or keys. Server addresses can change.

## 5. Optional: import the Proton file into NetworkManager on the host

This is **not needed** for the Docker/Kind environment. If explicitly desired:

```bash
python3 scripts/proton-wireguard.py import-host \
  "$HOME/Documents/wg-example.conf" --name wg-example
```

The command checks if a profile already exists and refuses to duplicate it. It will normalize a long `.conf` name into a temporary `wg-example.conf` automatically, prompt for `IMPORT-HOST`, and then invoke `nmcli conn import type wireguard file ...`. The temporary file is mode `0600` and is removed afterward. NetworkManager may change DNS and routes at import/activation. Reconnecting and disconnecting explicitly remain `nmcli connection up wg-example` / `nmcli connection down wg-example`.

**Do not add the previous single-port `nftables` policy routing rules** to this project: routing *only a chosen port* on the host is a separate, opt-in advanced networking feature, and the Pod is intentionally configured for all egress through the VPN.

## References

- [Proton VPN: Manual WireGuard on Linux](https://protonvpn.com/support/wireguard-linux)
- [Proton VPN: Download WireGuard configurations](https://protonvpn.com/support/wireguard-configurations)
- [Gluetun: Proton VPN provider](https://github.com/qdm12/gluetun-wiki/blob/main/setup/providers/protonvpn.md)
- [Gluetun: Secret-file variables](https://github.com/qdm12/gluetun-wiki/blob/main/setup/advanced/docker-secrets.md)
