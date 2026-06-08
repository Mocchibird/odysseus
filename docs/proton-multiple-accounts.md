# Adding more Proton Mail accounts

How to connect a **second (or third…) Proton Mail account** to Odysseus — e.g.
when another user on your instance also has Proton. For the *first* account, see
the "Proton Mail Bridge with Docker" section in the main [README](../README.md).

## How it fits together

- **Proton Bridge** holds the Proton *logins* and exposes each one as a local
  IMAP/SMTP mailbox with its **own bridge username + password** (not your Proton
  account password).
- **Odysseus email accounts are per-user** (owner-scoped). Each Odysseus user
  adds *their own* mailbox under their own login — an admin can't add it for
  them. The bridge login (Docker access) is done once by whoever runs the host;
  they hand the bridge credentials to that user.
- **One bridge can host multiple Proton accounts** — this is supported by
  Proton ([official docs](https://proton.me/support/multiple-accounts-bridge)).
  Note: the community `shenxn/protonmail-bridge` image only documents the
  single-account flow, so if the same-container path misbehaves, use the
  **second-container fallback** in Step 1b below.

> **Requirement:** each Proton account that uses Bridge needs an eligible
> **paid Proton Mail plan** (Bridge is a paid feature — confirm on Proton's
> current pricing).

---

## Step 1a — Add the account to your existing bridge (try this first)

The `proton-bridge` service stores its config in the `proton-bridge-data`
volume. Add the new account using the same interactive CLI flow you used for the
first account, run against that existing volume:

```bash
# From the directory with your docker-compose.yml (or via Dockge's stack shell):
docker compose stop proton-bridge                 # free the volume
docker compose run --rm proton-bridge init        # interactive Bridge CLI
#   In the CLI:
#     login        → enter the NEW account's Proton email, password, and 2FA
#     info         → copy this account's bridge USERNAME + PASSWORD (you need these)
#     list         → confirm both accounts now appear
#     exit
docker compose up -d proton-bridge                # bring the bridge back up
```

`info` prints each account's local IMAP/SMTP **username and password** — copy the
ones for the new account; you'll paste them into Odysseus in Step 2.

If `list` shows both accounts and the bridge restarts cleanly, you're done with
the bridge — skip to Step 2.

## Step 1b — Fallback: run a second bridge container

Use this if the same-container path won't take a second account, or if you want
each account fully isolated. Add a second service + volume to your compose file
(different host ports so they don't clash):

```yaml
  proton-bridge-2:
    build:
      context: ${ODYSSEUS_REPO_DIR:-.}
      dockerfile: docker/proton-bridge.Dockerfile
      args:
        PROTON_BRIDGE_BASE: ${PROTON_BRIDGE_IMAGE:-docker.io/shenxn/protonmail-bridge:latest}
    image: ${ODYSSEUS_PROTON_BRIDGE_IMAGE:-odysseus-proton-bridge:local}
    ports:
      - "${PROTON_BRIDGE_BIND:-127.0.0.1}:1144:143"   # note: 1144 / 1026, not 1143 / 1025
      - "${PROTON_BRIDGE_BIND:-127.0.0.1}:1026:25"
    volumes:
      - proton-bridge-data-2:/root                    # its OWN volume
    restart: unless-stopped
```

Then add the volume to the `volumes:` block at the bottom of the file:

```yaml
volumes:
  # …existing volumes…
  proton-bridge-data-2:
```

Log the account in (same flow, new service):

```bash
docker compose run --rm proton-bridge-2 init   # login → info → list → exit
docker compose up -d proton-bridge-2
```

This second bridge is reachable from Odysseus on the Docker network as
**`proton-bridge-2:143`** (IMAP) and **`proton-bridge-2:25`** (SMTP). The
`1144` / `1026` host binds are only for debugging from the host.

---

## Step 2 — Add the mailbox in Odysseus

Done by **the user who owns this mailbox**, logged into *their own* Odysseus
account (accounts are per-user):

1. **Settings → Email → Add account.**
2. **Choose the Proton preset:**
   - If the account lives on the **shared sidecar bridge** (Step 1a): pick
     **`Proton Bridge (Docker)`** — it fills IMAP/SMTP host `proton-bridge`,
     ports `143` / `25`, STARTTLS.
   - If you used a **second bridge** (Step 1b): set the host manually to
     **`proton-bridge-2`**, IMAP port `143`, SMTP port `25`, STARTTLS.
3. **Fill in the credentials from `info`:**

   | Field        | Value                                                        |
   |--------------|--------------------------------------------------------------|
   | Name         | anything, e.g. `Proton (alice)`                              |
   | Username     | the **bridge username** for this account (from `info`)       |
   | Password     | the **bridge password** for this account (from `info`)       |
   | From address | the Proton email address                                     |

   ⚠️ Use the **bridge** username/password from `info` — *not* the Proton
   account password, and *not* the first account's bridge credentials. Each
   account has its own.
4. **Save.** The form checks `/api/email/proton-bridge/status` so you can see
   whether IMAP/SMTP are reachable before saving. Send/receive a test email to
   confirm.

That's it — the new mailbox is isolated to that user, and Iris's email triage
(summaries, auto-tag, reply drafts) works on it like any other account.

---

## Troubleshooting

- **"Authentication failed" on save:** you almost certainly pasted the Proton
  account password or the wrong account's bridge password. Re-run
  `docker compose run --rm proton-bridge init` → `info` and copy the exact
  username/password for *this* account.
- **IMAP/SMTP unreachable from Odysseus:** the host must be the **container
  name** on the Docker network (`proton-bridge` or `proton-bridge-2`), ports
  `143` / `25` — not `1143` / `1025` (those are host-only debug binds).
- **Bridge won't restart after login:** rebuild the wrapper image
  (`docker compose build proton-bridge`) so self-updates have `libfido2.so.1`,
  then `docker compose up -d proton-bridge`.
- **Multiple *addresses* on ONE account** (aliases) vs a separate *account*:
  that's Bridge's combined/split-addresses mode, set inside the CLI — see
  [Proton's docs](https://proton.me/support/difference-combined-addresses-mode-split-addresses-mode).
  Adding a different person's Proton is a separate **account** (`login` again),
  which is what this guide covers.
