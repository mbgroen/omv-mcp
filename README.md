# omv-mcp

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant manage an
**OpenMediaVault** NAS.

Ask in plain language — *"which disks are unhealthy?"*, *"why is the SMB share not
mounting?"*, *"restart file sharing"* — and the assistant works through OpenMediaVault's
own API to answer or act.

It connects over ordinary SSH and drives `omv-rpc`, the command-line tool that ships with
OpenMediaVault. That is the same RPC layer the web interface uses, so anything you can do
by clicking, the assistant can do too. **Nothing is installed on the NAS and no extra port
is opened.**

```
MCP client  --stdio-->  omv_mcp.py  --ssh-->  NAS  -->  omv-rpc  -->  OMV RPC layer
 (your PC)              (your PC)                        (the same layer as the web UI)
```

**Works with any MCP client.** This is a plain stdio MCP server with no client-specific
behaviour: Claude Desktop, Claude Code, Cursor, Zed, Cline, VS Code, the OpenAI Agents SDK
and anything else that speaks the protocol can all run it. Any client needs the same three
things — a Python interpreter, the path to `omv_mcp.py`, and a handful of environment
variables.

For **Claude Desktop** specifically there is also a prebuilt `.mcpb` extension file. It
contains that very same server, wrapped so the app can install it in one click and give you
a settings panel instead of a config file to edit. It is a convenience, not a different
product — and not a requirement.

**No dependencies.** The server is pure standard library — including its MCP protocol
layer — so there is no virtualenv to create, nothing to `pip install`, and it runs on any
Python 3.9 or newer.

---

## What it can do

Six tools, and between them they reach the whole of OpenMediaVault:

| Tool | Purpose |
|---|---|
| `omv_connection_info` | Show the active configuration and test connectivity |
| `omv_list_services` | List every RPC service, including ones added by plugins |
| `omv_list_methods` | List the methods of one service |
| `omv_call` | Call an RPC method — the main tool |
| `omv_wait_for_task` | Collect the output of a background job |
| `omv_shell` | Run an arbitrary shell command on the NAS (off by default) |

In practice that covers disks and S.M.A.R.T. health, filesystems and mount points, shared
folders and their permissions, users and groups, the file-sharing services (SMB, NFS, FTP,
rsync), network settings, scheduled jobs, package updates, and applying pending
configuration changes.

### Why only six tools

OpenMediaVault exposes roughly 50 RPC services with several hundred methods between them,
and every plugin adds more. Rather than wrapping each one in a hand-written tool, omv-mcp
stays generic: *discover the services*, *discover a service's methods*, *call a method*.
The assistant explores the API the way a developer would, starting from what your
particular installation actually offers.

The practical benefit: any plugin you install later — Docker/Compose, ZFS, KVM,
Kubernetes, whatever — works immediately, with no update to this server.

---

## Requirements

| | |
|---|---|
| **NAS** | OpenMediaVault 7 or 8, reachable over SSH |
| **Your computer** | Python 3.9 or newer |
| **MCP client** | Any client that can run a stdio MCP server |
| **Access** | An SSH key that logs in to the NAS without a password, and an account that may run `omv-rpc` |

Developed and verified against **OpenMediaVault 8.5.6-1 (Synchrony)** on Debian 13. OMV 7
uses the same RPC layer and is expected to work; reports welcome.

### Platform support

| Your computer | Extension (`.mcpb`) | Manual setup | Notes |
|---|---|---|---|
| **macOS** | Yes | Yes | Python 3.9 ships with the system; `ssh` is present |
| **Windows** | Yes | Yes | Python is **not** included — install it from [python.org](https://www.python.org/downloads/) or the Microsoft Store first. `ssh` ships with Windows 10 1809 and later. |
| **Linux** | — | Yes | Claude Desktop is released for macOS and Windows only, so there is no extension to install. Everything else works: use Claude Code or any other MCP client. |

The server is verified on macOS (Python 3.9) and Linux (Python 3.13). Windows is covered
by CI but has not been run against a real NAS — reports welcome.

---

## Two ways to install

Both routes install the same server. Pick one:

| | Use this when | What you get |
|---|---|---|
| [**Claude Desktop extension**](#install-as-a-claude-desktop-extension) | You use Claude Desktop on macOS or Windows | One click, and a settings panel instead of a config file to edit |
| [**Manual setup**](#install-manually) | Any other client, or Linux | Clone the repository and point your client at `omv_mcp.py` |

The extension is simply this repository's `omv_mcp.py` packaged into a `.mcpb` file — a zip
with a manifest that tells Claude Desktop how to start it and which settings to ask you
for. Same code, same behaviour; only the installation differs. If your client is not Claude
Desktop, you are not missing anything by going the manual route.

---

## Install as a Claude Desktop extension

The extension gives you a settings panel, so nothing has to be edited in any file. macOS
and Windows only; on Linux use [the manual setup](#install-manually).

1. Download `omv-mcp-<version>.mcpb` from the [latest release](../../releases/latest).
2. Double-click it, or drag it onto the Claude Desktop window. It is also available under
   *Settings → Extensions → Advanced settings → Install Extension…*
3. Fill in at least the **NAS hostname or IP address**, then enable the extension.

| Setting | Default | What it does |
|---|---|---|
| NAS hostname or IP address | — | Required. An IP address, or a host defined in your `~/.ssh/config`. |
| SSH username | `root` | The account used to log in |
| SSH port | `22` | Change only for a non-standard port |
| SSH private key (path) | *(empty)* | Best left empty — that uses your ssh-agent and `~/.ssh/config`. See [SSH setup](#ssh-setup). |
| OpenMediaVault user | `admin` | The OMV login the RPC runs as — not the SSH user |
| Run commands with sudo | off | Turn on when the SSH user is not root |
| **Read-only mode** | **on** | Refuses anything that is not a read operation |
| Allow arbitrary shell commands | off | Adds the unrestricted `omv_shell` tool |
| Command timeout | `60`s | Per-command limit |

> **After changing any setting: click Save, then fully quit Claude Desktop (⌘Q on macOS)
> and reopen it.** The server reads its settings once at startup, so a running server keeps
> the values it was started with — closing the window is not enough. Until you restart,
> changes appear not to have saved: writes keep getting refused and `omv_connection_info`
> reports the old values.

Read-only mode is on by default. Turn it off once you are comfortable letting the
assistant change things.

### Building the bundle yourself

```bash
git clone https://github.com/mbgroen/omv-mcp.git
cd omv-mcp
python3 scripts/build_mcpb.py
```

The `.mcpb` lands in `dist/`. It is an ordinary zip archive with a `manifest.json` at its
root, so the build script needs nothing but the standard library — no Node.js and no
`mcpb` CLI.

---

## Install manually

The route for every client other than Claude Desktop — and the only route on Linux.

```bash
git clone https://github.com/mbgroen/omv-mcp.git
```

There is nothing to install. Point your client at `omv_mcp.py` with your system Python.
Whatever the client, it needs the same three things: the command `python3`, the argument
`/absolute/path/to/omv_mcp.py`, and the environment variables from
[the table below](#environment-variables). The examples that follow are just those three
things written in each client's own configuration format.

**Claude Code** — the same on macOS, Linux and Windows:

```bash
claude mcp add openmediavault -e OMV_SSH_HOST=192.168.1.100 -e OMV_READONLY=1 -- python3 "$PWD/omv_mcp.py"
```

Add `-s user` to make it available in every project rather than only the current one.
`claude mcp list` reports `✔ Connected` once the handshake succeeds.

**Cursor, Zed, Cline, VS Code, the OpenAI Agents SDK and others** — most use a JSON block
in the same shape as the Claude Desktop one below, under whatever key that client calls its
server list. Consult its documentation for the file, then fill in the same command,
argument and environment variables.

**Claude Desktop by hand** — useful if you would rather not use the extension, in
`claude_desktop_config.json` —
`~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows:

```json
{
  "mcpServers": {
    "openmediavault": {
      "command": "python3",
      "args": ["/absolute/path/to/omv-mcp/omv_mcp.py"],
      "env": {
        "OMV_SSH_HOST": "192.168.1.100",
        "OMV_SSH_USER": "root",
        "OMV_RPC_USER": "admin",
        "OMV_READONLY": "1"
      }
    }
  }
}
```

Use absolute paths, not `~`, and restart the client completely afterwards.

### Environment variables

The extension sets these for you; this table is for manual setups.

| Variable | Default | Meaning |
|---|---|---|
| `OMV_SSH_HOST` | *(empty)* | Hostname or IP of the NAS. Empty runs commands locally — use that if the server itself runs on the NAS. |
| `OMV_SSH_USER` | `root` | SSH user |
| `OMV_SSH_PORT` | `22` | SSH port |
| `OMV_SSH_KEY` | *(empty)* | Path to a private key. Empty uses your ssh-agent and `~/.ssh/config`. |
| `OMV_RPC_USER` | `admin` | The OMV user the RPC call runs as |
| `OMV_SUDO` | `0` | `1` prefixes every command with `sudo`, for non-root SSH users |
| `OMV_READONLY` | `0` | `1` refuses anything that does not look like a read method, and disables `omv_shell` |
| `OMV_ALLOW_SHELL` | `1` | `0` removes the `omv_shell` tool entirely |
| `OMV_TIMEOUT` | `60` | Per-command timeout in seconds |

Booleans accept `1`/`true`/`yes`/`on` and their opposites.

### Running on the NAS itself

Leave `OMV_SSH_HOST` empty and commands run locally instead of over SSH, so the server can
live on the NAS alongside OpenMediaVault. This only helps when the MCP client also runs
there — Claude Code over an SSH session, for instance — because stdio means the client
starts the server as a child process. `omv_connection_info` reports `"mode": "local"` when
it is working this way.

---

## SSH setup

The server runs `ssh` with `BatchMode=yes`, so password logins will not work. That is
deliberate: an MCP server has no way to prompt you for a password. Use a key.

```bash
ssh-keygen -t ed25519 -C "omv-mcp"        # skip if you already have a key
ssh-copy-id user@nas                      # your NAS address
ssh user@nas 'omv-rpc -u admin System getInformation'
```

If that last command prints JSON, the server will work. Run it whenever something is
wrong: it separates an SSH problem from an MCP problem in one step.

**Which key goes where.** The **public** key (`.pub`) belongs *on the NAS*, in
`~/.ssh/authorized_keys` — that is what `ssh-copy-id` puts there. The **private** key stays
on your computer and is what proves your identity. So if you fill in the key setting, give
it the path to the *private* key: `~/.ssh/id_ed25519`, not `~/.ssh/id_ed25519.pub`.

Better still, leave that setting empty. Then ssh uses your agent and `~/.ssh/config`, which
is where this kind of detail belongs:

```
Host nas
    HostName 192.168.1.100
    User root
    IdentityFile ~/.ssh/id_ed25519
```

and set the NAS address to `nas`. Note that leaving the setting empty only works if ssh can
find the key on its own — that means a standard name (`id_ed25519`, `id_rsa`), an entry in
`~/.ssh/config`, or a key loaded into your ssh-agent. A key with a custom name and no config
entry has to be given explicitly.

**First connection from a new computer.** The NAS will not be in that machine's
`known_hosts` yet, and `BatchMode` stops ssh from asking whether to trust it. omv-mcp
therefore connects with `StrictHostKeyChecking=accept-new`: an unknown host is recorded on
first contact, exactly as answering *yes* would, while a host key that **changes** later is
still refused. To approve it yourself instead, run `ssh user@nas` once in a terminal before
enabling the extension.

**Root login refused?** OpenMediaVault disables SSH root login by default. Either enable it
under *Services → SSH → Permit root login*, or use your own account and turn on sudo — that
account needs to be in the `sudo` group.

---

## Using it

Start by asking the assistant to check the connection; it will call `omv_connection_info`
and report your OpenMediaVault version. From there, ask for what you want in ordinary
language:

- *"Are any of my disks reporting SMART errors?"*
- *"What is using space on the data pool?"*
- *"Show me the shared folders and who can write to them."*
- *"The SMB service is not responding — what does its status say?"*
- *"Are there package updates pending, and what would they change?"*
- *"Apply the pending configuration changes."*

Under the hood the assistant calls `omv_call` with a service and a method:

```python
omv_call("System", "getInformation")
omv_call("FileSystemMgmt", "enumerateMountedFilesystems", {"includeRoot": True})
omv_call("ShareMgmt", "enumerateSharedFolders")
omv_call("Smart", "getListBg", {"start": 0, "limit": -1})
```

You do not need to memorise service names — `omv_list_services` and `omv_list_methods`
always reflect your own installation, plugins included. Service names are case sensitive,
and a few are lowercase (`kernel`, `omvextras`), so use them exactly as returned.

**Background jobs.** Heavier methods return `{"filename": "..."}` and keep running on the
NAS. `omv_wait_for_task` polls until the job finishes and returns the accumulated output:

```python
result = omv_call("Apt", "upgrade")           # -> {"filename": "/tmp/bgstatus..."}
omv_wait_for_task(result["filename"], max_seconds=600)
```

**The shell tool** is an escape hatch for everything outside the RPC layer — `journalctl`,
`docker ps`, `smartctl`, package state. Prefer `omv_call` for anything OpenMediaVault
manages itself, so its configuration database stays in sync with the system. Off by
default.

---

## Security

**This server can do anything you can do in the OpenMediaVault web interface, and — if you
enable the shell tool — run arbitrary commands as root.** That is the point of it, but be
clear-eyed about what it means: the only thing between a mistaken suggestion and a wiped
filesystem is the tool confirmation dialog in your MCP client. Read what it says before
approving.

Sensible precautions:

- **Keep read-only mode on** until you have a feel for what the assistant does with it.
- **Leave the shell tool off** unless you need it.
- **Use a dedicated SSH key** for this rather than your everyday key.
- **Consider a non-root user** with sudo and a narrowed sudoers rule.
- **Keep it on your LAN.** This server has no authentication of its own; its security
  boundary is your SSH configuration.

Read-only mode uses a prefix heuristic — a method is allowed if its name starts with `get`,
`enumerate`, `list`, `is`, `has`, `read`, `query`, `find`, `exists`, `count` or `check`. It
is deliberately conservative and will occasionally block a harmless method. It is a guard
rail, not a security boundary: it cannot stop a read method that happens to have side
effects.

Service and method names are validated against `^[A-Za-z0-9_]+$`, and every value
interpolated into a shell command goes through `shlex.quote`, so parameters cannot break
out into the shell.

On host keys: the connection uses `StrictHostKeyChecking=accept-new`, which trusts the NAS
on first contact and pins it from then on. That is trust-on-first-use, the same bargain you
make when you type *yes* at an ssh prompt, and it is what makes an unattended first
connection possible. It is **not** `StrictHostKeyChecking=no`: a key that changes after that
first connection is still refused. Connect once from a terminal beforehand if you want to
avoid the first-use window entirely.

---

## How it works

### Discovery

An RPC service's name is not its file name — it is whatever the PHP `getName()` method
returns. So `omv_list_services` greps the sources in
`/usr/share/openmediavault/engined/rpc/` and reads the names out. The result is cached for
the lifetime of the process; restart the server after installing a plugin.

Three details about OpenMediaVault's sources that this parser handles, and which are easy
to get wrong when writing your own:

1. **Quoting is inconsistent.** The PHP mixes `'` and `"` freely. On OMV 8.5, six of the 52
   service names and about a third of all `registerMethod()` calls use single quotes.
   Matching only double quotes silently loses them.
2. **Some service names are lowercase** — `kernel` and `omvextras`, for instance — and
   `omv-rpc` is case sensitive.
3. **One file can hold several RPC classes.** `notification.inc` defines both `Notification`
   and `EmailNotification`, so methods are scoped to the class block they appear in rather
   than to the whole file.

Errors are cleaned up too: a failed `omv-rpc` call writes a JSON blob to stderr containing a
full PHP stack trace, and only the `message` field is surfaced.

### The MCP layer

`mcp_stdio.py` implements the protocol directly: newline-delimited JSON-RPC 2.0 over stdio,
`initialize` with version negotiation, `tools/list` with input schemas derived from each
function's signature and docstring, and `tools/call`.

That is a deliberate choice rather than an exercise. The official MCP Python SDK depends on
pydantic, which ships compiled binaries — and the MCPB documentation is explicit that you
[cannot portably bundle compiled dependencies](https://github.com/modelcontextprotocol/mcpb#python-servers).
Implementing the handful of methods a tools-only server needs keeps the extension a single
small file that works on macOS, Windows and Linux alike, with no runtime to install and no
Python version floor beyond 3.9.

---

## Tests

No dependencies, nothing to install:

```bash
python3 -m unittest discover -s tests -t tests -v
```

Or with pytest, if you prefer its output:

```bash
pip install pytest && pytest
```

The fixtures under `tests/fixtures/` are real output captured from an OpenMediaVault 8.5.6-1
system — grep dumps of the RPC sources, a successful RPC response and an error response —
with identifying values replaced. The tests therefore assert against what a NAS actually
returns rather than against an idealised sample.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Extension will not install | Python 3.9 or newer is missing. Check with `python3 --version`. |
| Extension will not start | No `python3` on PATH (`python` on Windows) |
| Server does not appear in the client | JSON syntax error, or a relative path in a manual config |
| `Permission denied (publickey)` | This computer's key is not in `authorized_keys` on the NAS, or you gave the `.pub` instead of the private key |
| `Host key verification failed` | The NAS key could not be accepted automatically — run `ssh user@nas` once in a terminal |
| No connection from a newly set up computer | That machine has no SSH key on the NAS yet. Test with `ssh user@nas 'omv-rpc -u admin System getInformation'`. |
| `No RPC services found` | The SSH user cannot read `/usr/share/openmediavault/engined/rpc` |
| `command not found: omv-rpc` | `/usr/sbin` is not in the SSH user's PATH — turn on sudo |
| `Command exceeded 60s` | Raise the timeout, or use `omv_wait_for_task` for long jobs |
| A method is refused as "not a read method" | Read-only mode is on. If you just turned it off, fully quit and reopen Claude Desktop. |
| Settings disagree with `omv_connection_info` | The server still holds its startup values, or a second registration in another client answered |
| A setting will not stick | Quit Claude Desktop and edit the extension's JSON under `~/Library/Application Support/Claude/Claude Extensions Settings/`, then reopen |
| A newly installed plugin is invisible | Restart the MCP server; the service list is cached |

Claude Desktop writes per-server logs to `~/Library/Logs/Claude/` on macOS.

---

## Contributing

Issues and pull requests are welcome — particularly reports from OpenMediaVault 7, from
plugins whose RPC sources are laid out unusually, and from setups where discovery finds
fewer services than the web interface offers.

Please run the test suite before opening a PR. If you are fixing a parsing issue, add a
fixture captured from the real system alongside it; that is how the existing tests are
built.

## License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with or endorsed by the OpenMediaVault project.
