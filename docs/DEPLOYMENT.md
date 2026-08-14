# Deploying it

Two people use this: **Shilpan (owner)** on a Mac, and the **accountant** on
Windows. One machine holds the books.

## The shape

```
        ┌────────────────────────────────────────────┐
        │  OFFICE SERVER  — always on, one machine   │
        │  Urjapod service + the database + PDFs     │
        │  http://urjapod-server:8765                │
        └───────────────┬────────────────────────────┘
                        │  office LAN (or Tailscale from home)
            ┌───────────┴────────────┐
     accountant's PC             your Mac
     browser only                browser only
     role: accountant            role: owner
```

**Do not install the server on the accountant's PC.** Roles are enforced by the
API, and the API can only enforce them if the accountant does not hold the
database file. Give him the file and he can open it with any free SQLite
browser and edit whatever he likes; the audit trail becomes decorative. One
host, and he gets an interface that refuses him instead of a file he can open.

The same reasoning is why there is no "each person has their own copy" mode:
two copies means two sets of books, which is not accounting.

## What each person installs

| | Installer | Choose | What it does |
|---|---|---|---|
| Office server | `Urjapod-Setup-x.y.z.exe` | **Server** | Windows Service that starts at boot, opens the firewall port, creates the owner account |
| Accountant | the same `.exe` | **Workstation** | A shortcut that opens his browser at the server. No data stored locally |
| You (Mac) | `Urjapod-x.y.z.dmg` | drag to Applications | Opens a browser at the server. Can also run as the server if the office box is a Mac |

Both installers are built by GitHub Actions on real Windows and macOS runners
(`.github/workflows/release.yml`). Tag a release to produce them:

```bash
git tag v0.2.0 && git push --tags
```

The files appear on the GitHub Releases page.

### First run on the server

1. Run the installer, choose **Server**, accept the default port `8765`.
2. Tick **Allow other PCs on the office network to reach this server**.
3. At the end it offers **Create the owner account** — do it. That account is
   the only one that can void, delete, and approve amendments.
4. Note the machine's name (`hostname` in a command prompt). The accountant
   will need `http://<that-name>:8765`.

### First run on the accountant's PC

Run the same installer, choose **Workstation**, and type the server URL. He
gets a desktop shortcut and nothing else — no database, no documents, nothing
to tamper with.

### Adding him as a user

Sign in as owner → **Users** → *Add a user*, role **Accountant**, with a
temporary password. He is asked to change it when he first signs in.

## What the accountant can and cannot do

| | Accountant | Owner |
|---|---|---|
| Create orders, invoices, POs, receipts | ✅ | ✅ |
| Issue documents | ✅ | ✅ |
| Amend an **issued** document | proposes — waits for you | applies at once |
| Approve or reject an amendment | ❌ | ✅ |
| Void a document | ❌ | ✅ |
| Delete a document | ❌ | ✅ |
| Edit customers, suppliers, items | ✅ | ✅ |
| Edit **GST rates** | ❌ | ✅ |
| Edit Tally ledgers, company profile | ❌ | ✅ |
| Manage users | ❌ | ✅ |
| Reports, audit log | ✅ | ✅ |

There is a third role, **Viewer** — read-only — for handing your CA access at
year end without giving them the ability to change anything.

Every refusal is recorded. If the accountant repeatedly tries to void invoices,
that shows up in the audit log.

## How changes are recorded

An issued document is frozen. Changing it never overwrites the row:

```
version 1  ─ issued          ─ current      ← what the customer received
version 2  ─ amended by ca   ─ pending      ← ledger still shows version 1
                 owner approves
version 1  ─ superseded
version 2  ─ current         ← now in effect, version 1 still readable
```

Reprinting version 1 still produces the original PDF exactly, because the
render context is frozen per version. **Documents → History** on any invoice
shows the whole chain: who changed what, when, and why.

Changing a quantity or rate re-runs the tax engine, so GST is recalculated from
the HSN codes and the customer's state rather than being edited around. The
approval screen shows the resulting figures before you agree to them.

### Void versus delete

**Void** is the right tool almost always. The document drops out of the ledger,
the reports and the Tally export, but keeps its number so the invoice series
stays gapless — which is what an auditor checks.

**Delete** physically removes it. It is available to you because you asked for
it, and it is deliberately awkward: owner only, a typed confirmation of the
invoice number, and a mandatory reason. Before anything is removed, a complete
copy — header, every line, every version — is written to the audit log, so what
was deleted stays recoverable even though the entry is gone.

One thing to weigh, in your hands not mine: from FY 2023-24 a private limited
company must keep an edit log that cannot be disabled. A hard delete removes
the entry from the books, and the gap in the numbering is the first thing a
statutory auditor asks about. The audit record survives, so the trail is not
broken — but void achieves the same practical result without the question being
raised at all. Use delete for things that should never have existed, like a
duplicate created during setup.

## Backups

**The single most important thing on this page.** The books live on one
machine; if that disk fails, they are gone.

- Start menu → **Back up the books**, or `urjapod backup`
- Writes a consistent copy of the database (safe while the server is running)
  plus a zip of all generated PDFs and uploaded POs
- **Copy the result off that machine** — a backup on the same disk is not a
  backup. A OneDrive/Google Drive folder or a USB drive is fine.

Schedule it: Windows Task Scheduler → daily → `"C:\Program Files\Urjapod\urjapod.exe" backup --to D:\Backups`

Uninstalling does **not** delete the books. They stay in `ProgramData\Urjapod`
until removed deliberately.

## Working from home

The server is on the office LAN, so it is not reachable from outside by
default — which is the safe default. To reach it from home, install
[Tailscale](https://tailscale.com) on the server and on both laptops; it gives
each machine a stable private address with no port forwarding and nothing
exposed to the internet. The URL becomes the Tailscale name instead of the LAN
one.

Do **not** forward port 8765 on the office router. The app speaks plain HTTP on
the LAN; exposing that to the internet would send passwords in the clear.

If you ever do put it behind HTTPS, set `SESSION_COOKIE_SECURE=true` — and only
then. On plain HTTP a Secure cookie is never sent and nobody can stay signed in.

## Upgrading

Run the newer installer over the top. Schema migrations run at startup. The
data directory is untouched. Take a backup first anyway.

## If something goes wrong

| Symptom | Where to look |
|---|---|
| Accountant cannot reach the server | `ping urjapod-server`; check the firewall rule exists |
| "not signed in" immediately after signing in | `SESSION_COOKIE_SECURE` is true but you are on plain HTTP |
| PDFs fail to generate on Windows | the GTK3 runtime did not install; re-run the installer |
| Service not running | Services → *Urjapod Orders & Invoicing*; log at `ProgramData\Urjapod\server.log` |
| Forgotten owner password | on the server: `urjapod create-owner --force` |

## Known gaps

- **The installers are built but not yet run on real hardware.** The CI
  workflow verifies PDF rendering works on each platform before packaging, but
  the Inno Setup service installation and the macOS bundle need one manual test
  on a real machine before you rely on them.
- **Unsigned.** Windows shows a SmartScreen warning; macOS needs right-click →
  Open the first time. Removing those needs a code-signing certificate
  (~₹15–30k/yr) and an Apple Developer account (~$99/yr).
- **SQLite by default.** Fine for two users. If it ever feels slow, point
  `DATABASE_URL` at the Postgres in `docker-compose.yml`; nothing else changes.
