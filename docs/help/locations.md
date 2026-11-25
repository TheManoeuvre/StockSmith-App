# Locations

Locations say **where** your stock is kept. StockSmith already knows how much of everything you
have; locations add where to find it. They're optional, and you can set them up at any time.

## What locations are

A location is a place: a room, a shelf, a box. Locations nest, as deeply as you like, so
`Workshop › Oils rack › Bin 1` is a bin on a rack in a workshop. One level is fine too: just
`Box A1`.

You build the tree yourself on the **Locations** page, under **Stock** in the sidebar. There are no
fixed levels to fill in and nothing to configure first.

Each location has a scan code, like `LOC-000042`. It never changes, even if you rename or move the
location, so a printed label stays valid.

## Add locations

- **Add location** makes one. Choose what it sits inside, or leave it at the top level.
- **Add child** (on a selected location) makes one inside it.
- **Generate range** makes a whole run at once. See below.

## Generate a range

To make Box01 to Box05 without typing five names, use **Generate range** and write a pattern with
the changing part in braces:

| Pattern | Makes |
|---|---|
| `Box{01-05}` | Box01, Box02 … Box05 (the leading zero is kept) |
| `Bin-{A-F}` | Bin-A, Bin-B … Bin-F |
| `Shelf {1-3}` | Shelf 1, Shelf 2, Shelf 3 |
| `{A-D}{1-6}` | A1, A2 … D6: a grid of 24 |
| `Col{AA-AD}` | Col AA, Col AB, Col AC, Col AD |

Letters carry on past Z (…Y, Z, AA, AB…), and the number of zeros in the first number sets the
padding. Use at most two ranges in one pattern, and at most 500 locations at a time.

You see every name before anything is saved. Names that already exist in that place are shown
struck through and skipped, never overwritten. If you change your mind straight after, **Undo**
archives the new ones.

## Placing and moving stock

**Placing** says how much of an item is in a location. Do it from the item itself (its **Stored in**
section, on a material's Stock tab or a product's Stock section) or from a location with **Place an
item here**.

You can split an item across several locations: 5 spools on the shelf and 20 in the loft. Stock
you haven't placed anywhere is shown as **Unassigned**. That's normal, not a fault, and it's where
every existing item starts.

One location per item is its **primary** one, the place you take from first. The first place you
put an item becomes its primary; change it with **Make primary**.

A product that has variants is placed per variant, because the variants hold the stock.

### Moving

- **Move an item** (from a location's contents or an item's Stored in section) moves some or all of
  one item's stock between two locations. The item's total never changes.
- **Move location** puts a location inside a different one: Box01 from Shelf 1 to Shelf 2. Everything
  in it comes along.
- **Empty into…** takes everything out of one location and merges it into another. The first
  location stays, empty.

Each move is recorded in the location's **History**.

## Stock that follows itself

If an item is placed in **exactly one** location, StockSmith keeps that quantity right for you:
builds and sales take stock off it, and deliveries, returns and corrections add to it.

Items placed in **several** locations, or none, aren't changed automatically, because StockSmith
can't know which place you took from. See the next section.

## When the numbers drift

If more is placed than is in stock (say you used some from a split item), the item is marked
**Drift** in amber. This is routine and doesn't block anything. Use **Reduce … by …** to take the
extra off, or move stock to the right place.

Anything with a drift also shows on the Locations page under **Attention**.

## Import and export

**Export CSV** on the Locations page saves your whole layout as a spreadsheet, one row per
location, and **Import CSV** reads one back. It is the quickest way to set up a big layout, or to
move one between computers.

| Column | Meaning |
|---|---|
| `path` | Where it is, written as `Workshop > Oils rack > Bin 1`. Required. |
| `code` | The scan code. For reference only: it is never read on import, because codes can't change. |
| `notes` | Optional note. A blank cell leaves an existing note alone. |
| `archived` | `yes` to archive, `no` to bring one back, blank to leave it. |

Importing makes any parent that's missing along the way, so a file can list just the deepest rows.
A location that already exists is matched by its path (capitals don't matter), never duplicated.
A row with a problem is skipped and listed with its row number, and the rest still import.

The file holds the layout only, not what's stored in it.

## Printing labels

Each location can have a label with its name, where it sits, and a barcode of its scan code.
Stick them on shelves and boxes, and a scanner (one that types, like most USB and Bluetooth ones)
can jump straight to a location: scan into **Scan or type a LOC- code** at the top of the Locations
page.

1. On the Locations page, select a location and choose **Print label**. Or, straight after
   generating a range, choose **Print labels for these**.
2. On the print screen, tick the locations you want. **Include everything beneath each tick** adds
   the locations inside them too.
3. Pick a size and check the preview, then **Print**. Each label prints on its own page.

Two sizes are built in, and each has its own layout:

- **51 × 25 mm** (the default): the location's own name leads, with where it sits as a small line
  above. Long names are cut short with an ellipsis, and a long path drops its outermost parts
  first, so the nearest one always stays.
- **6 × 4 in**: the full path is the headline, so it works as a shelf or bay sign from across the
  room, with a large barcode.

Choose **Custom** for any other size, in millimetres. It uses whichever layout is closer. The size
you pick is remembered on this computer, and you can also set it under **Settings › Labels**.

Set your printer's paper size to match the label before printing. If labels come out cut off or
on the wrong size of page, that is almost always the printer's paper size setting.

## Archiving and deleting

A location that has never held anything can be **deleted**. Once it has, it can only be
**archived**, which keeps old history meaningful. A location must be empty, with its own
locations archived or moved, before you can archive it. Show archived locations with **Show
archived** under the tree; you can unarchive one at any time.

## See also

- [How StockSmith thinks](concepts.md)
- [Stock takes](concepts.md#stock-takes)
