# Shared secrets (SOPS + age)

Team secrets (API keys, etc.) are stored **encrypted** in this repo and
decrypted locally at runtime. The ciphertext is safe to commit; the only thing
shared out-of-band is one age **private key**.

- **`secrets.sops.env`** — the encrypted secrets (committed). Looks like a
  dotenv file but every value is `ENC[...]`.
- **`.sops.yaml`** (repo root) — which age public key(s) can decrypt.
- **`with-secrets.sh`** — runs any command with the secrets injected as env
  vars (nothing hits disk in plaintext).

## Install (everyone)

```bash
brew install sops age
```

## Bootstrap (ONE person, first time)

1. Generate the team age key in the location sops checks for your OS
   (**macOS:** `~/Library/Application Support/sops/age/`, **Linux:**
   `~/.config/sops/age/`) and note the public key it prints:
   ```bash
   # macOS:
   KEYDIR="$HOME/Library/Application Support/sops/age"
   # Linux:  KEYDIR="$HOME/.config/sops/age"
   mkdir -p "$KEYDIR"
   age-keygen -o "$KEYDIR/keys.txt"               # prints "Public key: age1..."
   ```
2. Paste that `age1...` public key into `.sops.yaml` (replace the placeholder), commit it.
3. Create + encrypt the secrets file. This opens your `$EDITOR`; type dotenv
   lines, save, and sops encrypts on save (plaintext never touches disk):
   ```bash
   sops secrets/secrets.sops.env
   ```
   In the editor, add:
   ```
   TIKTOK_CLIENT_KEY=<your client key>
   TIKTOK_CLIENT_SECRET=<your client secret>
   ```
4. Commit the encrypted file:
   ```bash
   git add .sops.yaml secrets/secrets.sops.env && git commit -m "chore(secrets): add shared TikTok API creds (encrypted)"
   ```
5. Share the **private key** — the contents of your `keys.txt` — with teammates
   via your password manager / a secure channel. **Never** paste it in
   Slack/email/chat or commit it.

## Onboard (each teammate)

```bash
brew install sops age
# macOS:
KEYDIR="$HOME/Library/Application Support/sops/age"
# Linux:  KEYDIR="$HOME/.config/sops/age"
mkdir -p "$KEYDIR"
# paste the shared private key into this file:
$EDITOR "$KEYDIR/keys.txt"
git pull
```

(If you keep the key somewhere else, set `SOPS_AGE_KEY_FILE=/path/to/keys.txt`
in your shell profile — `with-secrets.sh` also auto-detects both default
locations.)

## Use it

```bash
# Run any command with the secrets present in its environment:
secrets/with-secrets.sh .venv/bin/python -m scripts.social_stats.tiktok_api fetch --account dailytrivia
```

The Python tools read these from the environment, so they also still work with
a local `.env` for solo dev — `with-secrets.sh` is just the team path.

## Add or rotate a secret

```bash
sops secrets/secrets.sops.env      # edit; saves re-encrypted
git add secrets/secrets.sops.env && git commit -m "chore(secrets): update"
```

## Better: one key per teammate (no shared private key)

Instead of sharing one private key, have each person run `age-keygen` and send
you their **public** key. List all public keys in `.sops.yaml` under `age:`
(comma-separated), then re-encrypt to all recipients:

```bash
sops updatekeys secrets/secrets.sops.env
```

Now each person decrypts with their own key, and removing someone is just
deleting their public key and re-running `updatekeys`.

## Rotation note

The committed ciphertext lives in git history. If the age **private** key ever
leaks, rotate it: generate a new key, `updatekeys`, **and** rotate the actual
secret values (e.g. regenerate the TikTok client secret in the developer
portal), since old history is still decryptable with the old key.
