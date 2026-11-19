# Backups, restoring and moving to a new computer

Everything StockSmith knows — materials, products, orders, images — is kept on your computer.
Backups are how you protect it, and how you move it to another computer.

## What a backup contains

A backup is a single zip file holding your database and every product and material image.

**Store connections are deliberately left out.** Your Square token and Etsy/eBay logins aren't in
the backup, so a backup file never contains anything that can access your shops. After
restoring onto a different computer, connect your stores again — nothing else is lost.

## Automatic backups

Backups run every day by default, while StockSmith is open. If the computer was asleep at the
scheduled time, the backup runs shortly after it wakes. Change the time and how many backups to
keep in **Settings › Backup & restore › Schedule**. **Back up now** makes one straight away.

Backups are saved in `%LOCALAPPDATA%\StockSmith\backups` (paste that into the File Explorer
address bar to open it).

## Keep a copy somewhere else

A backup on the same computer doesn't help if that computer fails, is lost or is stolen.

In **Settings › Backup & restore**, set **Also copy each backup to** to a folder that syncs
somewhere else — OneDrive, Dropbox or Google Drive. Every backup is then copied there too. If
StockSmith can't reach that folder, it tells you.

## Restoring a backup

**Settings › Backup & restore › Restore** lists the backups StockSmith can see — both the ones on
this computer and the ones in your "also copy to" folder.

1. Choose **Restore this** next to the backup you want.
2. Type `RESTORE` to confirm, and choose **Restore and restart**.

Restoring replaces everything currently in StockSmith. A snapshot of your current data is taken
first, so a restore can be undone by restoring that snapshot.

## Moving to a new computer

1. **On the old computer**, make sure a recent backup has been copied to your OneDrive, Dropbox or
   Google Drive folder (see [Keep a copy somewhere else](#keep-a-copy-somewhere-else)). Click
   **Back up now** first to make sure it includes your latest changes.
2. **On the new computer**, install StockSmith and sign in to the same OneDrive, Dropbox or Google
   Drive so the folder syncs down.
3. When StockSmith asks what you make, choose **Restore a backup instead**.
4. Set **Also copy each backup to** to that same folder. Your backups appear under **Restore**.
5. Restore the most recent one.
6. Connect your stores again in **Settings › Stores & sync**.

No cloud folder? Copy the backup zip onto the new computer (a USB stick works) and put it in
`%LOCALAPPDATA%\StockSmith\backups`, then restore it from the list.

**Stop using StockSmith on the old computer once you've moved.** Two copies both syncing the same
stores will both reserve stock for the same orders and push competing quantities to your
listings.

## Starting again from scratch

To reset StockSmith to a brand-new empty install, quit it from the notification-area icon, then
delete the `%LOCALAPPDATA%\StockSmith` folder. A fresh one is created next time it opens. This
deletes everything, including the backups stored on this computer — copy any you want to keep
first.
