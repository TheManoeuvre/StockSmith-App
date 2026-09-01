# Changelog

All notable changes to StockSmith are recorded here.

The release workflow reads the section matching the tag being built and uses it as the
GitHub Release body, which in turn becomes the `notes` field in `latest.json` — that's
what the in-app update prompt shows. So whatever is written here is what users read when
deciding whether to install an update: write it for them, not for the commit log.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.9.0] - 2026-09-01

A new look for the whole app: navigation moves to a sidebar, and opening a product,
material, order, purchase or stock take no longer leaves the list behind for a new page —
it slides a panel over it instead.

### Changed
- **Navigation moved from a top bar to a sidebar**, and every screen picked up a denser,
  more consistent visual style — tabs, dialogs, buttons and status pills all restyled to
  match.
- **Clicking into a product, material, order, purchase or stock take now opens it in a
  panel over the list, instead of navigating to a full page.** The list stays put behind
  it, so closing the panel or stepping to the next/previous item never re-fetches or loses
  your place. Each one is still its own URL — a direct link or a bookmark opens straight to
  the right item — and leaving a panel with unsaved changes still asks first, the same as
  before.
- **Materials, orders and purchases now split their detail view into tabs**, matching
  products and stock takes: Materials into Details / Purchasing / Counting / Stock, Orders
  into Lines / Financials / Shipping, and Purchases into Lines / Receiving history.

## [0.8.0] - 2026-08-22

Two orders currently stuck out of sync — dispatched on the marketplace before StockSmith could allocate them — will now record a real postage cost once mapped and shipped, instead of silently reading as free. The products list can now show you this kind of gap before it reaches an order: sale price, materials, packaging and postage cost, together, with a filter for what's still missing.

### Fixed
- **Orders dispatched on the marketplace before they were allocated here shipped with no
  postage cost, making them look more profitable than they were.** When a marketplace SKU
  isn't recognised, the order line has no product attached — so there is nothing to allocate
  (which is the "shows as shipped, but no units are allocated" warning) and, less visibly,
  no shipping profile to take a postage cost from. Mapping the line by hand fixed the first
  problem but never the second, so the order shipped with postage counted as £0. It now
  resolves the shipping profile whenever a line is mapped, created or re-allocated. 26 of
  this shop's shipped orders are affected, overstating profit by about £95 between them.
- **A shipping profile assigned after an order had already shipped never took effect.** The
  order would show the profile's name against a blank postage cost, and because shipping
  can't be changed on a shipped order there was no way to correct it. The cost is now
  recorded at the point the profile is resolved. Four orders were in this state.
- **Multi-unit orders that got their stock from a build were charged for a box per unit.**
  Packaging is meant to be counted once per parcel. That default was applied when an order
  was allocated, but not when a build handed it stock — so an order whose packaging was set
  up after it arrived, then filled from a build, consumed three boxes and three labels for a
  single parcel. Two orders were affected.

  **Orders already shipped are not corrected automatically.** They keep the figures they
  shipped with; ask for them to be repaired if you want the history restated.
- **Creating an Etsy draft could fail with "A readiness_state_id is required for physical
  listings."** Etsy's own API documentation lists that field as optional, but the live
  endpoint refuses a physical draft without one. A listing profile now needs a processing
  profile picked, same as it already needs a shipping profile, and the draft panel says so
  up front instead of letting the create call fail.

### Added
- **Orders missing a postage cost now say so.** A shipped order that never recorded what
  postage cost is marked "No postage cost" on the orders list and explains itself on the
  order, instead of quietly reporting a profit that leaves it out. Separate from the
  existing "COGS pending" mark, which means something else and is fixed a different way.
- **The products list now shows what each product costs to sell.** Sale price, then
  materials, packaging and postage stacked in one column, with the shipping profile named
  underneath. For a product with variations each figure shows the range across them — the
  list previously showed the base recipe's cost, which for some products matched no
  variation actually sold.
- **Products with missing cost information can be found in one place.** A count beside the
  products list says how many are missing a shipping profile or a materials recipe, and
  filters down to exactly those. This shop has 9. Products genuinely sent without packaging
  are not counted — no packaging is a real answer, not a gap.
- **The product margin estimate no longer treats missing postage as free.** Where no
  shipping profile is set, the margin is marked as excluding postage rather than quietly
  reading £0.

## [0.7.2] - 2026-08-19

Deliveries can now be recorded as they actually turn up, line by line, instead of a
purchase order being all-or-nothing — and each one is costed at the date it arrived. Count
sheets are grouped the way your shelves are, and things there is no point counting can be
left off them.

### Added
- **Deliveries are recorded line by line, as they actually turn up.** A supplier who sends 6
  of 10 now and the rest next month can be recorded that way, instead of forcing a choice
  between two wrong stock figures. "Record a delivery" on the purchase order lists what's
  still outstanding, filled in with all of it, and you change only what differs.
- **Split deliveries cost correctly.** Each delivery is priced at the date it arrived, so a
  material's average cost reflects when the stock actually landed rather than pretending it
  all came at once. Leave the cost of a delivery blank and it takes its share of the order
  line; fill it in when the supplier billed that delivery separately.
- **A short delivery can be closed off.** When the rest of a line is never coming, close it:
  it stops counting as on order and the purchase can complete, without rewriting what you
  originally ordered. You're asked whether you were charged for the full quantity anyway.
- **One delivery can be undone without undoing the order.** Each is listed on the purchase
  with its own Undo, so a mis-keyed figure doesn't mean reversing everything and starting
  again.
- **Count sheets are grouped the way your shelves are.** Products by category, then by their
  parent SKU so a product's variations sit together; then materials, in the category order
  you've arranged under Settings. Before this it was one long alphabetical list with every
  kind of thing mixed in, which meant walking back and forth. The downloaded sheet is in the
  same order, and groups fold away so you can work through one shelf at a time.
- **Products that are made to order can be left out.** Tick "Made to Order" on a product and
  it stops appearing on count sheets and on the list of things due for counting, for all of
  its variations. There's nothing on a shelf to go and count, so there's no point being
  asked.
- **The first delivery of something new counts as having counted it.** Buy a material you've
  never had before, receive it, and StockSmith takes the delivered quantity as verified — it
  knew there were none, and now it knows exactly how many arrived. It won't do this for
  something that merely ran out and was restocked: that zero was a belief, not a count, and
  checking it is the whole point.

### Changed
- **An order's discount now reads as part of what was paid, not as another deduction.** The
  order page listed Order value paid, Postage paid and Discount side by side, which looked
  like the discount came off the total a second time — it doesn't, and never did, because
  the order value shown was already net of it. The discount now sits under the figure it
  explains, as "£6.99 − £1.40 discount", so the top row is simply the figures that add up to
  the net profit beneath it. The shipping profile moves under Postage cost for the same
  reason, instead of sitting in its heading.
- **The Products list is grouped by category**, matching how the Materials list has always
  worked, with a heading and a count for each. What 0.7.1 called a product "type" is now a
  product "category" throughout — it was the same idea as a material category under a
  different name, on two pages meant to read alike. Anything you'd already set is carried
  over.
- **"On order" now means what's still to come**, not the whole order, everywhere it appears:
  the materials list, buildability, packaging capacity and the stock forecast. A
  part-delivered order used to be counted twice — once as stock on the shelf and again as
  something still on its way.

### Fixed
- **eBay discounts were not being recorded, so those orders looked more profitable than they
  were.** eBay reports the item total before any discount and the discount separately;
  StockSmith was reading the discount from the wrong field name, which returned nothing
  without ever failing. Any eBay order with a multi-buy or volume discount therefore counted
  the discounted money as revenue. Ten of this shop's eBay orders were affected, overstating
  profit by £15.36 between them. New orders are now correct, and the figures are checked
  against what eBay says the buyer actually paid so a gap like this says so instead of
  sitting quietly. Etsy was never affected — it takes its discount off before StockSmith
  sees the order, which is why its net profit has always been right.

  **Orders already imported are not corrected automatically** — a sync only revisits recent
  orders, so the affected ones are exactly the ones it skips. Ask for them to be re-read if
  you want the history put right.
- **A material's history now adds up to its stock figure.** Purchases were listed against the
  date they were ordered, and orders that hadn't arrived were mixed in with things that had
  actually moved stock, so the rows never summed to the quantity shown above them.
  Deliveries and adjustments now account for it exactly, and what's still on order is shown
  separately.
- **Editing a purchase order no longer loses what's been received against it.** Saving any
  change to a purchase — even just its notes — used to rebuild all of its lines from
  scratch.

**Before updating, take a backup** (Settings → Backups). This release changes how material
stock and cost are worked out from your purchase history. The upgrade checks itself as it
runs and was verified against a copy of a real database, but a backup is the thing that
makes it reversible.

## [0.7.1] - 2026-08-18

StockSmith can now count stock properly. Say how often each thing is worth checking, and
it tells you what's come round, gives you a count sheet to work from — on screen or on
paper — and shows you every difference before it changes anything. Material categories
also stop being a fixed list of seven: add your own, and set what each one does.

### Added
- **StockSmith now tells you what needs counting.** Stock drifts from what the app thinks
  you have — a dropped print, a miscount, something used and never recorded — and until now
  the only way to find out was to check everything. You can now say how often each thing is
  worth counting, and the dashboard lists what's come round. Nothing is blocked or nagged
  about; it's a list to work from.
- **Set how often by group, not one item at a time.** Give a whole category a tier — resin
  every month, packaging every three — and everything in it follows, including any category
  you've added yourself. Anything unusual can be set on its own and overrides the group.
  Each item's page says which of the two it's currently getting, so it's clear where to go
  and change it.
- **Products can have a type.** Keyring, coaster, whatever you sell — products had no way of
  being grouped before, only a name and a SKU. Set one on the product page or manage the list
  under Settings > Reference data; the Products list gains a column and a filter for it.
  Types are also what lets products be scheduled for counting by group.
- Sensible counting intervals are set up already (30, 60 and 90 days by tier) so this is
  useful without configuring anything first. Change any of them under Settings > General.
- **Counting something by hand already counts.** Use "Set exact amount" on a material or
  product and StockSmith takes that as a physical count — it stops being listed as due and
  its count date moves to today, so you're not asked to do the same job twice. Adjusting
  by an amount doesn't do this: knowing two got broken isn't the same as having checked
  what's left.
- **Your existing counts are recognised.** Anything you've previously set an exact amount
  for starts out dated from when you did it, rather than the list arriving with your whole
  catalogue on it. Items you've never counted still say so.
- **Stock takes.** Pick what to count — materials, finished stock, or both, narrowed to a
  category or type, or just what's due — and StockSmith builds a count sheet and notes what
  it currently thinks you have. Fill it in on screen or export it, count with the sheet in
  your hand, and upload it back. Then it shows you every difference before anything is
  changed.
- **Differences it can't safely decide are handed to you, not applied.** Two cases: stock
  that moved while you were counting, and stock that's been picked for an order. The second
  matters more than it sounds — units boxed up ready to post have left the shelf but are
  still counted as stock until the order ships, so counting the shelf comes up short by
  exactly that much. Applying that would quietly write off goods sitting by the door.
  StockSmith flags them instead and tells you why, with three choices: use what you
  counted, keep the figure it has, or clear it and count again later. The count sheet also
  shows how many are already picked, so you can go and find the boxes and avoid the
  question.
- **A stock take can close with questions outstanding.** They don't hold it open — they
  move to an "unresolved variances" list, linked from the dashboard, so you can settle them
  whenever without having to remember which count they came from.
- **Uploads are shown to you first.** A spreadsheet with a typo in it doesn't half-apply:
  you see what read cleanly, what didn't and why, and choose between applying the good rows
  or fixing the file and starting again. Nothing is saved until you say so.
- **Rows left blank are left alone.** Not counted isn't the same as counted zero — a blank
  changes no quantity and doesn't mark the item as counted, so it stays on your list.
- **Material categories are yours to set up.** Settings → Reference data → Material categories
  lets you add, rename, reorder, merge and delete them, instead of being stuck with the seven
  that were built in. Your existing categories carry over exactly as they were, in the order
  they were already in.
- **Categories now carry their own behaviour.** The things StockSmith used to assume about
  filament and packaging are checkboxes on each category: whether a failed build still uses the
  material up, whether kitting counts it once per order rather than once per item, whether it
  has a colour and a material type, whether its cost reads per kilo, and which unit to default
  to. Set them on any category, including ones you add — so a new "Vinyl" can behave like
  filament, and resin can have a colour, which it never could before.

Counting is saved as you go, on the machine rather than in the window, so a stock take
survives closing StockSmith and picks up where you left it.

### Fixed
- **Clearing a field on a manufacturer, supplier or colour now sticks.** Emptying a website
  address or a hex code appeared to save and then came back the next time the row was opened.

## [0.7.0] - 2026-08-17

StockSmith now keeps syncing after you close the window, and can start with Windows —
so orders keep importing and stock keeps going out while you're doing something else.
Also fixes Etsy order syncing giving up when Etsy was slow to answer, which could leave
a shop unsynced for days.

### Added
- **StockSmith keeps syncing when you close the window.** Closing it now tucks it into the
  notification area at the right-hand end of the taskbar rather than shutting it down, so
  orders keep importing and stock keeps going out to Etsy and eBay while you get on with
  something else. Click its icon there to open it again, or use Quit on it to close
  StockSmith properly. The first time the window disappears you'll get a note explaining
  where it went, so it doesn't look like a crash.
- **It can start with Windows.** Settings → General → Background syncing. It starts straight
  into the notification area without opening a window. One thing worth knowing: this happens
  when you *sign in*, not when the PC powers on — so after an overnight Windows Update
  restart it only starts once somebody signs in, unless Windows is set to sign you back in
  automatically. The README explains how to turn that on.
- **You can see whether it actually stayed running.** The same panel lists any stretches in
  the last week where nothing synced at all — which is what a night the PC was off, or
  asleep, looks like. Before this, a quiet night and a night StockSmith wasn't running were
  impossible to tell apart.
- **It restarts its own engine if that stops.** The part of StockSmith that does the actual
  work used to be started once and never checked on again, so if it stopped, the app sat
  there looking fine and syncing nothing until somebody restarted it. It's now checked every
  half-minute and restarted if it has gone, backing off if it can't start.

### Changed
- **Closing the window no longer asks about unsaved work.** It doesn't need to — the window
  is still there with your half-finished form in it, waiting for you. Quitting from the
  notification area is the only thing that can lose anything now, and that asks first.

### Fixed
- **An engine left behind by a crash is cleared up instead of being left running.** If
  StockSmith was killed off — Task Manager, a power cut mid-shutdown — its engine could
  survive without it and quietly hold on to your database. The next launch now finds it and
  clears it away rather than starting a second one beside it.
- **Only one copy of StockSmith can run at a time.** Opening it again when it was already
  running in the notification area now brings the existing window back instead of starting a
  second copy competing for the same database.
- **Etsy order syncing no longer gives up when Etsy is slow to answer.** Part of each sync
  fetches the fee breakdown for an order. If Etsy didn't respond to one of those in time,
  the whole sync stopped — and because it stopped before recording its progress, the next
  attempt started from the same place and hit the same wall. A shop could sit like that for
  days. Now a slow fee lookup just means that order arrives without its fee breakdown yet,
  and everything else imports normally.
- **A failed sync now tells you why.** Some failures were recorded with no reason attached,
  so Settings → Integrations showed a sync marked as failed with nothing next to it. There
  is always a reason there now.
- **Automatic syncing can no longer stop without saying so.** A brief hiccup reading the
  database could switch off the background sync for a platform until the app was restarted,
  with nothing shown anywhere — it carried on reporting itself as connected while syncing
  nothing. It now rides out the hiccup and carries on with the next cycle.

## [0.6.3] - 2026-08-14

Fixes an update that could apply itself only halfway, and makes setting up listing profiles
possible without going and looking up id numbers by hand.

### Fixed
- **Updating no longer leaves you running half of the old version.** Windows can't replace
  a file that's in use, so if StockSmith was running when an update installed, the app
  updated but the part of it that does the actual work didn't — and the two carried on
  together looking almost fine. That's what made the Integrations page fail after updating
  to 0.6.2. StockSmith now notices and tells you to close it fully and run the installer
  again, rather than carrying on in a half-updated state. **If you're on 0.6.2 and the
  Integrations page is erroring, this is why — installing this version fixes it.**
- **The window closes when you click the X.** If StockSmith wanted to ask about unsaved
  work but couldn't show the question, the close was cancelled and nothing appeared, which
  left the X looking broken. It now closes rather than trapping you.

### Changed
- **Listing profiles are set up by name, not by id number.** Etsy identifies your category,
  shipping profile and return policy by numbers it never shows you anywhere in Etsy itself.
  Now you search for a category the way you would on Etsy, and pick your shipping profile
  and return policy from a list of your own. Return policies are described by what they do
  — "Returns and exchanges within 30 days" — because Etsy doesn't give them names.
- **Options read as words.** "I did" rather than `i_did`, and eBay's item condition is a
  list to choose from instead of something to type.
- **Etsy needs reconnecting once.** Reading your shipping profiles needs a permission
  StockSmith didn't previously ask for. Everything else works as before until you do.

## [0.6.2] - 2026-08-14

Groundwork for creating listings from StockSmith, plus the tool that makes it possible:
your Etsy listings already hold descriptions, prices and photos that StockSmith never
stored, and it can now read them back.

### Added
- **Fill in missing descriptions, prices and photos from Etsy.** Settings > Integrations >
  Check Etsy. It reads the listings your products are already linked to and offers what
  they hold: the description, the price of each variation, and the main photo. Tick what
  you want and it fills them in. It never overwrites anything you've already typed, so
  it's safe to run again later — the second time it simply finds less to do.
- **See which products won't fit Etsy's or eBay's rules before you try to list them.** The
  two stores cap things differently — a SKU can be 50 characters on eBay but only 32 on
  Etsy, a title 140 on Etsy and 80 on eBay — and StockSmith now checks your products
  against both and says which ones would be rejected, and by which store. It only appears
  when something needs attention.
- **Listing profiles.** Etsy and eBay both need details an inventory system never cared
  about: a category, who made the item, which postage policy applies. Most products answer
  these the same way, so a profile answers them once and products use it. StockSmith can
  suggest profiles by reading the listings you already have. On its own this doesn't do
  anything visible yet — it's the setup that lets StockSmith create a listing for you in a
  future update.
- **Separate listing titles from product names.** A product name is what you call it; a
  listing title is written for search and has a length limit. You can now keep both, and
  set a different one per store when Etsy's longer limit is worth using.
- **Correct a store's limits yourself.** If Etsy or eBay changes one — or one of
  StockSmith's built-in values turns out to be wrong — you can change it in Settings
  without waiting for an update.

### Fixed
- **Bulk-editing BOM overrides no longer quietly does nothing.** The "Value" box was free
  text, so a typo, a capital letter in the wrong place or a stray space matched no
  variants at all and the preview came back empty — which looked identical to there being
  nothing to change. It's now a list of the values actually in use.

## [0.6.1] - 2026-08-09

**Back up your data before updating.** This release changes the database and the app
applies that automatically on first launch. Nothing here is designed to lose data, but
this is the last update where you have to do that by hand — from now on StockSmith backs
itself up.

### Added
- **StockSmith now backs itself up.** Settings > Backup. One backup is a single zip
  holding your database and every product image. It runs automatically once a day while
  the app is open, keeps the last seven, and you can take one on demand at any time.
- **Copy each backup to a second folder.** Point it at OneDrive, Dropbox or anywhere that
  syncs, and your backups end up somewhere other than this computer — which is the copy
  that still helps if this computer is the thing that fails. If that folder ever stops
  working, StockSmith tells you instead of failing quietly.
- **Restore from a backup.** Choose one, confirm, and StockSmith restarts and puts your
  data back as it was. A snapshot of your current data is taken first, so a restore can
  itself be undone. Marketplace connections are deliberately not included in backups, so
  after restoring onto a different computer you'll need to reconnect Etsy and eBay —
  nothing else is lost. Restore runs on the computer hosting StockSmith; other devices
  wait and reconnect on their own.
- **Reference data can be edited.** Manufacturers, suppliers, material types and the new
  Colours list all live under Settings > Reference data, and open in place so you can
  correct several without leaving the page. Renaming one updates every material, purchase
  and order that uses it. Duplicates — the two spellings of one supplier that built up
  over time — can be merged into a single entry.
- **Colours are a real list now.** They used to be free text typed onto each material, so
  "Black", "black" and "BLACK" were three different colours. Existing values are merged
  automatically on first launch, keeping whichever spelling you used most.
- **Shipping profiles can be archived.** A profile you no longer offer disappears from the
  pickers while every past order it shipped under keeps its costs.
- **Unsaved changes are protected in Settings too.** Editing forecasting thresholds,
  marketplace credentials or backup settings and then navigating away now asks first,
  the same way the product and material pages already did.

### Changed
- **Products show one sellable figure instead of four raw numbers.** The list had three
  columns of buildable/sellable counts and the product page repeated them. There is now a
  single headline — what you can actually sell right now, which is also what's pushed to
  your marketplaces — with the breakdown in words underneath: how many are built and free,
  how many more you could build, and what open purchase orders would add.
- **Settings is reorganised.** General comes first and Connection last. Each marketplace's
  fee components moved onto that marketplace's own card under Integrations, and shipping
  profiles moved to Reference data. The Pricing tab keeps the one setting that really is
  shop-wide — which channel your margins are estimated for — and now shows which fees that
  choice actually applies.
- Copy buttons on product names and SKUs.

### Fixed
- **A quantity limit was reported as "nothing built" even when units were on the shelf.**
  The label only appears now when something genuinely external — packaging, or a platform
  quantity cap — is holding the figure down.
- **Deleting a shipping profile silently changed past orders.** It removed the profile from
  every product, variant and completed order that referenced it, quietly altering what those
  orders recorded being shipped under. Profiles in use can no longer be deleted; archive
  them instead.
- **StockSmith stopped writing its own log file after startup.** Applying database updates
  switched off the logging configured moments earlier, so `backend.log` was missing almost
  everything that happened after launch — which is exactly what's needed when diagnosing a
  problem.
- Reference data lists could never be corrected: there was no way to rename, merge or remove
  an entry once created, so a typo introduced by an import was permanent.

## [0.6.0] - 2026-08-06

**Back up your data before updating.** This release changes the database, the app
applies that automatically on first launch, and it cannot be undone.

**Two figures will move, on existing orders as well as new ones.** Net profit goes
**up** on any order with more than one item — packaging was charged per unit when only
one box was ever used. Product and variant margins read slightly **lower** — packaging
is now counted against them. Your stock, prices and orders are untouched; only the
arithmetic changed.

Orders shipped before this update need a one-off repair before their packaging cost
shows up — run `scripts/backfill_order_kitting_ledger.py` (dry run first, then
`--apply`). It moves no stock.

### Added
- **Fee reporting signature** for eBay, in Settings > Integrations. eBay requires UK and
  EU sellers to digitally sign requests for financial data; setting this up once is what
  lets StockSmith read your eBay fees. See
  [docs/ebay-fee-reporting.md](docs/ebay-fee-reporting.md).
- **Bill of Materials and Kitting BOM are now one tab**, with the two tables stacked and their
  columns lined up so you can read a product's build cost and packaging cost together.
- **Both tables now show cost.** Each line shows what its quantity costs at the material's
  current price and what share of the total that is, with a total under each table — so it's
  obvious at a glance which material is driving a product's cost.
- **Save buttons stay greyed out until there's something to save**, everywhere on the product
  page. Previously every Save button was always clickable, so it never told you anything.
- **You're now warned before losing unsaved edits** — switching tabs, collapsing a variant,
  changing the pricing mode, leaving the page, or closing the app window all ask first, and
  name what's unsaved. This covers the Materials pages too: editing a material's details, a
  half-typed stock adjustment, or a part-filled new-material form.
- **Save buttons on the Materials pages** follow the same rule as products — greyed until
  there's something to save, and stock adjustments stay disabled until they have both a value
  and a reason.

### Fixed
- **eBay platform fees were never imported.** Every eBay order showed its fees as "Not
  yet settled" indefinitely, even after eBay had taken them and made the payout
  available — because eBay was rejecting StockSmith's request for the fee breakdown and
  the failure was never reported anywhere. Net profit on every eBay order was overstated
  by the missing fee as a result. Fixing this needs a one-off setup step: Settings >
  Integrations > eBay > **Fee reporting signature**. Orders already imported keep their
  blank fees until backfilled — see
  [docs/ebay-fee-reporting.md](docs/ebay-fee-reporting.md).
- eBay fee data was also being requested from the wrong address (`api.ebay.com` rather
  than `apiz.ebay.com`), and the amount eBay pays out per order was being reduced by the
  fee a second time. Both were masked by the rejection above and are fixed together.
- An order with no fee figure now reads "Not reported yet" rather than "Not yet settled",
  which claimed to know something the app had no way of knowing.
- **Editing a product could silently discard your changes.** Saving anything on a product page
  refreshed the whole product, which wiped out unsaved edits in every other section — so typing
  a BOM quantity while a background refresh landed could lose it with no warning. Every editor
  on the page now keeps your edits until you save or discard them yourself.
- **A variant's name could show out of date.** Renaming a variant elsewhere never reached an
  open variant row, which kept displaying the old name until the page was reloaded.
- **Packaging was over-charged on every multi-unit order.** StockSmith has always known
  that an order shipping several units needs one box, not one per unit — the Kitting
  section showed exactly that — but cost of goods charged for a box per unit anyway. A
  three-unit order using a £1 box was charged £3 for packaging, so net profit was
  understated on every order with more than one item. Packaging cost is now taken from
  what the order actually consumed, and it moves when you change an override.
- **Cancelling a shipped order put back too much packaging.** The same per-unit assumption
  ran on returns, so cancelling a three-unit order returned three boxes to stock when only
  one had been used — and an order with two items returned the shared box twice.

### Changed
- **"Max theoretical" is now "Max from free stock"** in both BOM tables, and both count only
  material that isn't already reserved against an order. The build BOM previously counted all
  stock on hand, which read higher than what you could actually build today.
- **Cost of goods is now split into Materials and Kitting** on the order page, instead of
  one combined figure. A line's Cost is the materials to make it; packaging is shown once
  for the order, because that is how it is bought and used. The order's Kitting section
  now shows what each material costs and what the whole order's packaging comes to.
- **Product and variant margins now include packaging**, so they agree with the net profit
  shown on orders. Margins will read slightly lower than before — the packaging was always
  being paid for, it just wasn't counted here.
- **"Packaging" on the order page is now called "Kitting"**, matching the name used
  everywhere else.

### Notes
- Packaging cost is now frozen when an order ships, so a past order's profit no longer
  drifts as you re-buy boxes at new prices. Orders shipped before this update are valued
  at today's material cost, since their historical cost isn't recoverable.

## [0.5.0] - 2026-08-04

### Added
- Products list now has a **Stores** column showing a colour-coded badge per connected
  marketplace, so it's glanceable which stores each product is listed on.
- The menu bar now shows how long ago your stores last synced, with an alert badge when
  a sync failed or when stock updates aren't reaching a marketplace.
- Testing a store sync now flags any listing whose quantity has drifted from StockSmith's,
  with a **Push corrections** button to set the marketplace back to StockSmith's numbers.
  Testing itself stays read-only — nothing is pushed until you click.
- **Bulk-edit BOM overrides** on the Variants tab corrects a BOM line across every variant
  sharing an attribute value (e.g. every "Large"), instead of editing each one by hand. It
  previews exactly what would change before anything is written.

### Fixed
- Generating variants with conflicting BOM rules now explains the conflict and names the
  attribute value responsible, instead of failing with "Internal server error".
- Editing a variant's BOM now pushes the new stock figure to your marketplaces. Previously
  the corrected number stayed local until some other stock change happened to trigger a push.
- The **eBay variation** column on the Platform Sync tab is no longer always blank — it now
  shows each SKU's variation (e.g. "Model: Button Dual") in the same format as Etsy's.
- eBay listing status is now read from the actual listing rather than assumed active, so a
  listing that's ended, inactive or not yet published is reported as such. A sold-out
  listing still counts as active, since eBay keeps it live at quantity 0.
- Transient eBay server errors (`errorId 25001`, "Dependent service failure") are now
  retried automatically instead of failing immediately — this most often showed up when
  migrating a listing with **Migrate & link**.
- Products whose eBay listing has no title no longer render a blank cell on the Platform
  Sync tab; they show the same "—" placeholder as any other missing value.
- Platform Sync tables for Etsy and eBay now line up column-for-column regardless of how
  long either listing title is.

## [0.4.2] - 2026-08-03

### Fixed
- Migrating a multi-variation eBay listing no longer times out partway through.

## [0.4.1] - 2026-08-02

### Fixed
- The eBay reconnect banner could get stuck on screen with no way to clear it.

## [0.4.0] - 2026-08-01

### Added
- Listing adoption: link existing eBay listings that were never migrated to the Inventory
  API, and Etsy listings that aren't linked to a StockSmith product yet.

## [0.3.5] - 2026-07-31

### Added
- Materials now show a Weeks-of-Supply forecast in place of the old static reorder
  threshold.

## [0.3.4] - 2026-07-30

### Changed
- The auto-created shipping label was replaced with a configurable default kitting BOM.

## [0.3.3] - 2026-07-29

### Added
- Builds, adjustments and order fulfilment are now unified into a single Stock history.

### Changed
- Order-line COGS is snapshotted at first allocation and costed by shipped quantity.
- New products automatically get the default shipping label on their kitting BOM.
- Packaging kitting defaults to qty 1 for multi-unit orders.

## [0.3.2] - 2026-07-28

### Fixed
- Marketplace orders are only imported once payment has settled.
- SQLite foreign-key enforcement is on, so deleting an order cascades correctly.

## [0.3.1] - 2026-07-27

### Added
- Delete-order and deactivate-product buttons.

### Fixed
- Dashboard boot race and Orders connection-pool exhaustion.
- Orders and Products lists are paginated, with an index on order placement date.
