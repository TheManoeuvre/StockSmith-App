from fastapi import APIRouter, Depends, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_auth
from app.schemas.location import (
    ContentRowRead,
    EmptyIntoRequest,
    EmptyIntoResult,
    HistoryRead,
    ItemRefIn,
    ItemSummaryRead,
    LocationCreate,
    LocationDetailRead,
    LocationRead,
    LocationTreeRead,
    LocationUpdate,
    MoveItemRequest,
    PathSegment,
    PlaceRequest,
    PlacementRead,
    QtyRequest,
    RangePreviewName,
    RangePreviewRead,
    RangeRequest,
    ReplenishRead,
    StoredInRead,
)
from app.services import locations as locs
from app.services import placements
from app.services.locations_csv import export_locations_csv, import_locations_csv
from app.services.placement_hooks import ItemRef

router = APIRouter(prefix="/locations", tags=["locations"], dependencies=[Depends(require_auth)])
placements_router = APIRouter(prefix="/placements", tags=["locations"], dependencies=[Depends(require_auth)])


def _row_read(row: placements.LocationRow) -> LocationRead:
    n = row.node
    return LocationRead(
        id=n.id,
        parent_id=n.parent_id,
        name=n.name,
        code=n.code,
        notes=n.notes,
        archived=n.archived,
        own_items=row.own_items,
        rolled_up_items=row.rolled_up_items,
        needs_attention=row.needs_attention,
    )


async def _read_one(session: AsyncSession, location_id: int) -> LocationRead:
    tree = await placements.tree_summary(session)
    return _row_read(next(r for r in tree.rows if r.node.id == location_id))


@router.get("", response_model=LocationTreeRead)
async def list_locations(session: AsyncSession = Depends(get_db)) -> LocationTreeRead:
    tree = await placements.tree_summary(session)
    return LocationTreeRead(
        locations=[_row_read(r) for r in tree.rows],
        total_locations=tree.total_locations,
        attention_locations=tree.attention_locations,
        empty_locations=tree.empty_locations,
        unplaced_items=tree.unplaced_items,
        placeable_items=tree.placeable_items,
    )


@router.post("", response_model=LocationRead, status_code=status.HTTP_201_CREATED)
async def create_location(payload: LocationCreate, session: AsyncSession = Depends(get_db)) -> LocationRead:
    location = await locs.create_location(session, payload.name, payload.parent_id, payload.notes)
    return await _read_one(session, location.id)


@router.post("/range/preview", response_model=RangePreviewRead)
async def preview_range(payload: RangeRequest, session: AsyncSession = Depends(get_db)) -> RangePreviewRead:
    preview = await locs.preview_range(session, payload.parent_id, payload.pattern)
    return RangePreviewRead(
        names=[RangePreviewName(name=n.name, exists=n.exists) for n in preview.names],
        new_count=preview.new_count,
        error=preview.error,
    )


@router.post("/range", response_model=list[LocationRead], status_code=status.HTTP_201_CREATED)
async def generate_range(payload: RangeRequest, session: AsyncSession = Depends(get_db)) -> list[LocationRead]:
    created = await locs.generate_range(session, payload.parent_id, payload.pattern)
    tree = await placements.tree_summary(session)
    ids = {c.id for c in created}
    return [_row_read(r) for r in tree.rows if r.node.id in ids]


@router.get("/export")
async def export_locations(session: AsyncSession = Depends(get_db)) -> Response:
    return Response(
        content=await export_locations_csv(session),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=locations.csv"},
    )


@router.post("/import")
async def import_locations(file: UploadFile, session: AsyncSession = Depends(get_db)) -> dict:
    return await import_locations_csv(session, await file.read())


@router.get("/{location_id}", response_model=LocationDetailRead)
async def get_location(location_id: int, session: AsyncSession = Depends(get_db)) -> LocationDetailRead:
    d = await placements.location_detail(session, location_id)
    return LocationDetailRead(
        id=d.node.id,
        parent_id=d.node.parent_id,
        name=d.node.name,
        code=d.node.code,
        notes=d.node.notes,
        archived=d.node.archived,
        path=[PathSegment(id=i, name=n) for i, n in d.path],
        contents=[
            ContentRowRead(
                placement_id=c.placement_id,
                material_id=c.ref.material_id,
                product_id=c.ref.product_id,
                variant_id=c.ref.variant_id,
                name=c.name,
                sku=c.sku,
                unit=c.unit,
                qty=c.qty,
                is_primary=c.is_primary,
                drift=c.drift,
            )
            for c in d.contents
        ],
        children=[_row_read(r) for r in d.children],
        history=[
            HistoryRead(
                id=e.id,
                kind=e.kind,
                item_name=d.item_names.get((e.material_id, e.product_id, e.variant_id), "Item"),
                qty=e.qty,
                from_path=d.location_paths.get(e.from_location_id) if e.from_location_id else None,
                to_path=d.location_paths.get(e.to_location_id) if e.to_location_id else None,
                reason=e.reason,
                created_at=e.created_at,
            )
            for e in d.history
        ],
        history_count=d.history_count,
    )


@router.patch("/{location_id}", response_model=LocationRead)
async def update_location(
    location_id: int, payload: LocationUpdate, session: AsyncSession = Depends(get_db)
) -> LocationRead:
    fields = payload.model_fields_set
    if "name" in fields or "notes" in fields:
        await locs.update_location(
            session, location_id, name=payload.name, notes=payload.notes, set_notes="notes" in fields
        )
    if "parent_id" in fields:
        await locs.move_location(session, location_id, payload.parent_id)
    return await _read_one(session, location_id)


@router.post("/{location_id}/archive", response_model=LocationRead)
async def archive_location(location_id: int, session: AsyncSession = Depends(get_db)) -> LocationRead:
    await locs.set_archived(session, location_id, True)
    return await _read_one(session, location_id)


@router.post("/{location_id}/unarchive", response_model=LocationRead)
async def unarchive_location(location_id: int, session: AsyncSession = Depends(get_db)) -> LocationRead:
    await locs.set_archived(session, location_id, False)
    return await _read_one(session, location_id)


@router.delete("/{location_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_location(location_id: int, session: AsyncSession = Depends(get_db)) -> Response:
    await locs.delete_location(session, location_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{location_id}/empty-into", response_model=EmptyIntoResult)
async def empty_into(
    location_id: int, payload: EmptyIntoRequest, session: AsyncSession = Depends(get_db)
) -> EmptyIntoResult:
    moved = await placements.empty_into(session, location_id, payload.to_location_id)
    return EmptyIntoResult(moved=moved)


def _ref(p: ItemRefIn) -> ItemRef:
    return ItemRef(material_id=p.material_id, product_id=p.product_id, variant_id=p.variant_id)


@placements_router.get("/stored-in", response_model=StoredInRead)
async def stored_in(
    material_id: int | None = Query(None),
    product_id: int | None = Query(None),
    variant_id: int | None = Query(None),
    session: AsyncSession = Depends(get_db),
) -> StoredInRead:
    v = await placements.stored_in(session, ItemRef(material_id, product_id, variant_id))
    return StoredInRead(
        name=v.item.name,
        unit=v.item.unit,
        total=v.item.total,
        placed=v.placed,
        unassigned=v.unassigned,
        drift=v.drift,
        follows_stock=v.follows_stock,
        replenish=ReplenishRead(**vars(v.replenish)) if v.replenish else None,
        placements=[
            PlacementRead(
                id=p.id, location_id=p.location_id, location_path=p.location_path, qty=p.qty, is_primary=p.is_primary
            )
            for p in v.placements
        ],
    )


@placements_router.get("/summary", response_model=list[ItemSummaryRead])
async def item_summaries(session: AsyncSession = Depends(get_db)) -> list[ItemSummaryRead]:
    return [
        ItemSummaryRead(
            material_id=s.ref.material_id,
            product_id=s.ref.product_id,
            variant_id=s.ref.variant_id,
            name=s.name,
            sku=s.sku,
            unit=s.unit,
            primary_path=s.primary_path,
            extra_places=s.extra_places,
            placed=s.placed,
            total=s.total,
            drift=s.drift,
        )
        for s in (await placements.summaries(session)).values()
    ]


@placements_router.post("", status_code=status.HTTP_201_CREATED, response_model=PlacementRead)
async def place_stock(payload: PlaceRequest, session: AsyncSession = Depends(get_db)) -> PlacementRead:
    p = await placements.place(session, _ref(payload), payload.location_id, payload.qty)
    nodes = await locs.load_nodes(session)
    return PlacementRead(
        id=p.id,
        location_id=p.location_id,
        location_path=locs.path_names(nodes, p.location_id),
        qty=p.qty,
        is_primary=p.is_primary,
    )


@placements_router.post("/move", status_code=status.HTTP_204_NO_CONTENT)
async def move_item(payload: MoveItemRequest, session: AsyncSession = Depends(get_db)) -> Response:
    await placements.move_item(session, _ref(payload), payload.from_location_id, payload.to_location_id, payload.qty)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@placements_router.post("/{placement_id}/primary", status_code=status.HTTP_204_NO_CONTENT)
async def make_primary(placement_id: int, session: AsyncSession = Depends(get_db)) -> Response:
    await placements.make_primary(session, placement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@placements_router.post("/{placement_id}/reduce", status_code=status.HTTP_204_NO_CONTENT)
async def reduce_placement(
    placement_id: int, payload: QtyRequest, session: AsyncSession = Depends(get_db)
) -> Response:
    await placements.reduce_placement(session, placement_id, payload.qty)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@placements_router.delete("/{placement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_placement(placement_id: int, session: AsyncSession = Depends(get_db)) -> Response:
    await placements.remove_placement(session, placement_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
