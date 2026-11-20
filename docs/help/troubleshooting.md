# Troubleshooting

## "Windows protected your PC" when installing

The installer isn't code-signed, so Windows SmartScreen warns about it the first time. Click
**More info**, then **Run anyway**. This happens once and isn't a sign anything is wrong.

## StockSmith opens but shows "Reconnecting" and never loads

StockSmith runs a small background service on your computer, and the window waits for it.

1. **Check your antivirus.** The background service is an unsigned program that opens a port on
   your own computer (it only listens to your computer, never the network), which some antivirus
   tools quarantine. If yours has, StockSmith can't work at all. Restore the file and add an
   exclusion for `%LOCALAPPDATA%\StockSmith\` and the folder StockSmith is installed in.
2. **Check for an update.** The Reconnecting screen has a **Check for updates** button that works
   even when the service can't start. A fix may already be out.
3. **Quit and reopen.** Quit from the StockSmith icon in the notification area (closing the window
   only hides it), then open it again.
4. **Read the log.** `%LOCALAPPDATA%\StockSmith\backend.log` records why the service failed to
   start.

## Closing the window doesn't quit

That's deliberate. Closing the window leaves StockSmith running in the notification area so it
keeps syncing orders. To quit, right-click the StockSmith icon there and choose **Quit StockSmith**.

## Orders stop syncing overnight or after a restart

StockSmith only syncs while it's running. **Settings › Stores & sync › Background syncing** can start it
when you sign in to Windows, and lists any stretches in the last week when nothing synced.

If your computer is meant to sync around the clock, these Windows settings matter too. StockSmith
doesn't change any of them:

- **Sign back in automatically after an update** — Settings › Accounts › Sign-in options › "Use my
  sign-in info to automatically finish setting up after an update". Without it, an overnight
  Windows Update restart leaves the computer at the lock screen and StockSmith not running.
- **Sleep** — a sleeping computer isn't syncing. Settings › System › Power › Screen and sleep ›
  "make my device sleep after" = Never. Turning the screen off is fine.
- **Active hours** in Windows Update, so restarts happen overnight rather than mid-afternoon.

## An order is shipped on the marketplace but not in StockSmith

The Orders list marks these with a red **Shipped on Etsy** (or eBay, Square) badge, and the
**Store mismatch** filter lists them all. Their stock is still reserved, not taken out.

StockSmith ships an order itself when a sync sees the marketplace has shipped it, except when:

- **It needs a box check** — the items use more than one parcel box. Open the order and confirm
  which box you used; that ships it.
- **Nothing is allocated** — there wasn't stock for it when it synced. Allocate it, then click
  **Ship**.

Either way, the sync won't fix it later on its own: it only looks at orders the marketplace has
changed since the last sync.

## Where StockSmith keeps things

Everything is under `%LOCALAPPDATA%\StockSmith\` — paste that into the File Explorer address bar.

| Item | What it is |
|---|---|
| `data\stocksmith.db` | Your database: materials, products, orders, everything |
| `assets\` | Product and material images and files |
| `backups\` | Backups made on this computer |
| `config.json` | This install's private connection password and encryption key |
| `backend.log` | The background service's log, useful when it won't start |

`config.json` holds the key that encrypts your store logins. Don't share it, and don't copy it
between computers — use [a backup](backup-and-restore.md) instead.

## Updates

StockSmith checks for updates when it starts and asks before installing one. Installing restarts
the app. No internet, or no newer version, just means you're on the latest version.
