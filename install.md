# Installing dcc-mcp-wwise

This is the canonical standalone adapter runbook. Agents should read the
[raw file](https://raw.githubusercontent.com/dcc-mcp/dcc-mcp-wwise/main/install.md)
before changing an installation.

> [!IMPORTANT]
> The PyPI wheel is not published yet, and the Core catalog does not yet carry
> its pinned install URL and SHA-256. The wheel commands below are the canonical
> release path only after those publication gates exist. Until then, do not
> claim that a PyPI or catalog installation succeeded.

## Requirements

- Audiokinetic Wwise Authoring 2024.1 or newer.
- **Enable Wwise Authoring API** in **Project > User Preferences**.
- Python 3.10 or newer outside Wwise.
- `dcc-mcp-core>=0.20.14,<1.0.0` in the adapter environment.
- The default loopback endpoint `ws://127.0.0.1:8080/waapi`, or an
  operator-secured remote `wss://` endpoint whose hostname is listed in
  `DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS`.

The adapter uses Audiokinetic's official `waapi-client` and typed WAAPI calls.
It never installs Python or files inside Wwise and does not expose raw WAAPI
scripting or UI automation.

## Supported versions

| Platform | Supported topology | Wwise ownership |
| --- | --- | --- |
| Windows | Adapter and Wwise on the same machine over loopback | Audiokinetic Launcher |
| macOS | Adapter and Wwise on the same machine over loopback | Audiokinetic Launcher |
| Linux | Remote doctor/verify only | Remote Windows/macOS operator |

Wwise Authoring is not provisioned by this adapter. There is no “latest” page
scrape, downloaded host payload, or adapter-managed executable. Consequently,
there is no adapter-managed external binary cache to upgrade or clean.

Linux remote verification is preflight-only. The long-lived adapter service is
bound to a local Wwise Authoring PID, so run that service beside Wwise on
Windows or macOS rather than inventing a remote PID or weakening host lifetime
tracking.

## Agent quick path

First confirm that the published wheel and pinned Core catalog entry exist. Only
after that release gate passes, install the wheel:

```text
python -m pip install --upgrade dcc-mcp-wwise
```

Enable WAAPI in the intended Wwise project. A probe without a PID is deliberately
preflight-only:

```text
dcc-mcp-wwise doctor --json
```

On Windows, independently bind verification and the service to the intended
Wwise process rather than choosing among multiple projects:

```powershell
$wwisePid = (Get-Process Wwise | Where-Object MainWindowTitle -Like '*your-project*').Id
dcc-mcp-wwise verify --json --host-pid $wwisePid --timeout-ms 5000
dcc-mcp-wwise --host-pid $wwisePid
```

On macOS, use the exact Authoring process PID:

```text
wwise_pid="$(pgrep -x Wwise)"
dcc-mcp-wwise verify --json --host-pid "$wwise_pid" --timeout-ms 5000
dcc-mcp-wwise --host-pid "$wwise_pid"
```

## Manual path

1. Install Wwise 2024.1+ with Audiokinetic Launcher on Windows or macOS.
2. Open the intended project and enable **Wwise Authoring API**.
3. Keep the default loopback endpoint when the adapter is local.
4. For a deliberately remote topology, terminate TLS outside Wwise, set
   `DCC_MCP_WWISE_WAAPI_URL` to the resulting `wss://HOST:PORT/waapi` endpoint,
   and add only that hostname to `DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS`.
5. Run doctor and verify. They make only the typed
   `ak.wwise.core.getInfo` call through the official client; they do not start
   the DCC-MCP server or register the session menu.
6. Resolve the exact Wwise PID and start `dcc-mcp-wwise --host-pid PID`.

Step 6 runs on the Windows or macOS Wwise machine. A Linux verifier stops after
step 5 and must not substitute an unrelated local process for the Wwise PID.

Do not broaden the remote allowlist to unrelated hosts. Plaintext remote
`ws://` is rejected even when a hostname appears in the allowlist.

## Verify

```text
dcc-mcp-wwise doctor --json --host-pid PID --timeout-ms 5000
dcc-mcp-wwise verify --json --host-pid PID --timeout-ms 5000
```

Both verbs validate endpoint syntax and port, loopback/remote allowlist policy,
Core version, WAAPI enablement, the live typed runtime response, and the actual
Wwise version. Their stable exits are:

- `0`: the local loopback typed WAAPI probe passed and `directly_usable` is true;
- `10`: endpoint, allowlist, enablement, Core, Wwise-version, or host-binding
  preflight failed;
- `40`: a WAAPI session connected but the typed runtime call failed or returned
  an unusable response.

Remote WSS success returns exit `10` with `failure_stage: host_binding` and
`directly_usable: false`. Its machine-executable next step starts the PID-bound
adapter over loopback on the Windows or macOS Wwise host; a remote verifier must
not claim adapter readiness from `getInfo` alone.

Loopback success without `--host-pid` also returns exit `10`, with
`failure_type: identity_unavailable`. Exit `0` requires independently observing
the exact PID, executable name, and process start identity both before and after
the bounded typed probe. The WAAPI protocol does not currently attest its own
server PID, so this adapter does not claim that `getInfo` alone proves that
binding; the ecosystem-level contract remains tracked in
`dcc-mcp/dcc-mcp-core#2252`.

Failures contain `failure_stage`, `failure_type`, `failure_reason`, and one machine-executable
`next_steps[].command`. Connection success proves WAAPI is enabled and permits
the configured client; it does not modify the Wwise project.

After starting the adapter, the existing DCC-MCP discovery smoke is:

```text
dcc-mcp-cli list
dcc-mcp-cli search --query "Wwise WAAPI ping" --dcc-type wwise
dcc-mcp-cli load-skill wwise-project --dcc-type wwise
dcc-mcp-cli describe <returned-tool-slug>
dcc-mcp-cli call <returned-tool-slug> --json '{}' --wait
```

## Upgrade

Once the wheel is published, stop the adapter, upgrade it, and re-run doctor
before restarting:

```text
python -m pip install --upgrade dcc-mcp-wwise
dcc-mcp-wwise doctor --json --host-pid PID
```

Upgrade Wwise separately with Audiokinetic Launcher. The adapter never replaces
or downloads Wwise. Reconfirm the configured endpoint and exact process after a
Wwise upgrade.

## Uninstall

Stop the adapter first with Ctrl+C or SIGTERM. `stop_server()` unregisters the
session-scoped **DCC-MCP** menu and disconnects its WAAPI subscription; no Wwise
user configuration is retained. Then remove the published wheel:

```text
python -m pip uninstall dcc-mcp-wwise
```

There is no Wwise plug-in, daemon registration, receipt, host payload, or adapter
binary cache to remove. Wwise projects and Audiokinetic Launcher remain owned by
the operator.

## Troubleshooting

- `failure_stage: configuration`: correct the absolute `ws://`/`wss://` URL and
  numeric port, then run the emitted command.
- `failure_stage: endpoint_allowlist`: use loopback, or use remote `wss://` and
  add only the intended hostname to `DCC_MCP_WWISE_WAAPI_ALLOWED_HOSTS`.
- `failure_stage: waapi_enablement`: open the intended Wwise project, enable
  **Wwise Authoring API**, confirm its port/allowed clients, and retry.
- `failure_stage: core`: upgrade Core in the same Python environment after its
  distribution is available.
- `failure_stage: wwise_version`: upgrade Wwise through Audiokinetic Launcher;
  the adapter requires 2024.1+.
- `failure_stage: runtime`: WAAPI connected but rejected or malformed the typed
  `ak.wwise.core.getInfo` call. Inspect Wwise logs and retry without switching
  to raw scripting or UI automation.
- Multiple Wwise processes: pass the PID for the intended project explicitly.
- Session menu remains after an abnormal adapter termination: restart the
  adapter against the same project and stop it normally; startup unregisters
  stale adapter-owned command IDs before registering the current session.
