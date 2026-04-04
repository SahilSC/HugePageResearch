# Codex And Claude Terminal Install

This note is intentionally separate from the THP research docs. It records the local terminal-tooling setup that was installed on this machine so the same setup can be recreated later without touching `RESEARCH.md` or `MEMORY_BLOAT_README.md`.

## What Was Installed

- `nvm` in `~/.nvm`, checked out to tag `v0.40.3`
- Node.js `v24.14.1`
- npm `11.11.0`
- Codex CLI `0.117.0`
- Claude Code `2.1.86`

## Shell Integration

The install was made persistent by loading `nvm` from both shell startup files:

- `~/.bashrc` for interactive shells
- `~/.profile` for login shells

The `~/.profile` change matters on Debian/Ubuntu because the default `~/.bashrc` returns early in non-interactive shells, which means `bash -lc 'codex --version'` will not see Node unless `nvm` is also loaded from `~/.profile`.

## Recreate The Setup

Run these commands as the target user:

```bash
git clone https://github.com/nvm-sh/nvm.git ~/.nvm || true
cd ~/.nvm
git fetch --tags --depth=1 origin tag v0.40.3 || true
git checkout -f v0.40.3
grep -q 'export NVM_DIR="$HOME/.nvm"' ~/.bashrc || cat <<'EOF' >> ~/.bashrc
# Load nvm so user-local Node/npm are available in interactive shells.
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    . "$NVM_DIR/nvm.sh"
fi
EOF
grep -q 'export NVM_DIR="$HOME/.nvm"' ~/.profile || cat <<'EOF' >> ~/.profile
# Load nvm for login shells too, since Debian's default .bashrc exits early
# in non-interactive shells before our nvm block runs.
export NVM_DIR="$HOME/.nvm"
if [ -s "$NVM_DIR/nvm.sh" ]; then
    . "$NVM_DIR/nvm.sh"
fi
EOF
source ~/.profile
nvm install --lts
nvm alias default 'lts/*'
npm install -g @openai/codex
curl -fsSL https://claude.ai/install.sh | bash
```

## Verify

Open a fresh login shell, or run:

```bash
source ~/.profile
node -v
npm -v
codex --version
claude --version
```

Expected versions at the time this note was written:

```text
v24.14.1
11.11.0
codex-cli 0.117.0
2.1.86 (Claude Code)
```

## Use

In a new shell:

```bash
source ~/.profile
codex
claude
```
