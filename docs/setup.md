# Setting up ams-mcp

This guide is written so an agent can follow it alone, from top to bottom. Each step
has its commands and a `Check:` line, the command whose output proves that the step
worked. The steps under [Steps only the owner can do](#steps-only-the-owner-can-do)
need the owner, and each says why.

## What you need first

- A Linux or macOS computer with [uv](https://docs.astral.sh/uv/) and git.
- A meter reader running
  [AmsToMqttBridge](https://github.com/UtilitechAS/amsreader-firmware), on the same
  home network. The key names are checked against firmware v2.4.11.
- [claude-voice](https://github.com/roykollensvendsen/claude-voice), to ask by voice.
  Any other MCP client over stdio works too.

## Steps

### 1. Install the project

<!-- not run: it clones the repository and installs packages from the network -->
```bash
git clone https://github.com/roykollensvendsen/ams-mcp.git ~/ams-mcp
cd ~/ams-mcp
uv sync --locked
```

Check: `uv run ams-mcp --help` lists the `serve` and `ask` commands.

### 2. Hear the plugin read a recorded reader

This needs no reader. It answers from pages recorded off a real one, and says so.

<!-- safe-to-run -->
```bash
uv run ams-mcp ask now --simulate
```

Check: it prints what the voice would say, starting with `Simulated:`, and exits with 0.

### 3. Write the reader's address into the plugin's settings file

Use the name or address the owner found in step A. The file lives outside this
repository, so the address never reaches it. Skip this step if the file is already
there.

<!-- not run: it writes into the owner's own settings folder -->
```bash
mkdir -p ~/.config/ams-mcp
cat > ~/.config/ams-mcp/config.toml <<'EOF'
[reader]
address = "amsreader.invalid"   # the name or address from step A
timezone = "Europe/Oslo"        # optional; the computer's own by default
EOF
```

Check: `uv run ams-mcp ask now` prints a reading that does not start with `Simulated:`.

A name keeps working when the router hands the reader a new number, and many routers
cannot set one aside. If the router ever stops knowing the name, a question says so.

### 4. Add the plugin to claude-voice

Add this block to claude-voice's own settings file, `~/.config/claude-voice/config.toml`:

<!-- not run: it changes the owner's own bridge settings -->
```toml
[plugins.ams]
command = "uv"
args = ["run", "ams-mcp", "serve"]
cwd = "~/ams-mcp"
tools = ["ams_now", "ams_energy", "ams_price", "ams_capacity", "ams_history"]
timeout = 30
```

Check: `claude-voice plugins list` shows `ams`.

## Steps only the owner can do

### A. Find the reader's name or address in the router

<!-- owner -->
Why: the router's list of devices is behind the owner's own login, and the address
belongs to the owner's home network.

Look in the router's list of devices for the meter reader. Its name there, such as
`amsreader.invalid`, is the better choice. Use its IP address if the router shows no
name.

Check: `curl -s http://<the name or address>/data.json` prints a short JSON page.

### B. Approve the plugin in claude-voice

<!-- owner -->
Why: claude-voice offers a new plugin's tools only after the owner types its approval.

<!-- not run: it asks for typed input -->
```bash
claude-voice plugins approve ams
```

Check: `claude-voice plugins list` shows `ams` as `approved`.

## When the settings file is missing

A question says where the file should be:

```console
$ ams-mcp ask now --settings-file /nowhere/config.toml
ams-mcp: There is no settings file at /nowhere/config.toml. Write [reader] address = "..." there, or use --simulate.
```

`ams-mcp serve` still starts without it, so the voice sees the plugin. Every tool then
answers with the same advice and "Then restart the plugin." It says so once on its
error output, and serves until its input closes:

```console
$ ams-mcp serve --settings-file /nowhere/config.toml < /dev/null
ams-mcp: There is no settings file at /nowhere/config.toml. Write [reader] address = "..." there, or use --simulate.
```
