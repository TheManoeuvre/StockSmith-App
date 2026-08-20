"""Shared SQL over purchase lines and their receipts.

These fragments used to be copy-pasted — costing, buildability and kitting each carried
their own "on order" subquery, with a comment in each defending the duplication as this
codebase's convention for small self-contained fragments. That was a fair call while the
body was `WHERE p.status = 'ordered'`: one line, obviously right or obviously wrong.

It stopped being fair when receipts arrived. On-order is now ordered-minus-received over
an outer-joined aggregate with two predicates, and a copy that drifts doesn't fail — it
returns a plausible number that is quietly wrong, in a figure (buildability, kitting
capacity, the materials forecast) nobody cross-checks by hand. So there is one copy now.

Exported as plain strings rather than sa.text() because two of the three consumers embed
them inside larger f-string queries.
"""

# Ordered but not yet received, per material.
#
# A line contributes its outstanding remainder — what was ordered less what has arrived —
# and drops out entirely once it is closed short, because a closed line is a line nothing
# more is coming for. Note this reduces to the old behaviour exactly on data that predates
# receipts: an open line has no receipts, so outstanding == qty; a fully received line has
# receipts covering it, so outstanding == 0. That equivalence is what let the receipts
# migration land before anything read it.
ON_ORDER_BY_MATERIAL_SQL = """
    SELECT mp.material_id, SUM(mp.qty - COALESCE(r.received_qty, 0)) AS on_order_qty
    FROM material_purchases mp
    LEFT JOIN (
        SELECT purchase_line_id, SUM(qty) AS received_qty
        FROM material_purchase_receipts
        GROUP BY purchase_line_id
    ) r ON r.purchase_line_id = mp.id
    WHERE mp.closed_at IS NULL AND mp.qty - COALESCE(r.received_qty, 0) > 0
    GROUP BY mp.material_id
"""

# Received quantity per line, for anywhere that needs to know how much of a line has
# already landed (the API's outstanding figures, and the guards that refuse to shrink a
# line below what has been received).
RECEIVED_QTY_BY_LINE_SQL = """
    SELECT purchase_line_id, SUM(qty) AS received_qty
    FROM material_purchase_receipts
    GROUP BY purchase_line_id
"""
