export type MaterialUnit = "g" | "ml" | "each";
export type AssetType = "main_image" | "listing_image" | "step" | "threemf" | "gcode";
/** Time-to-stockout urgency from services/forecasting.py. "ok" only appears where the
 *  caller asked for every material (the materials list/detail), not on the dashboard. */
export type StockoutStatus = "critical" | "warning" | "insufficient_data" | "ok";

export interface Material {
  id: number;
  name: string;
  /** The category's name. A plain string now that the set of them is user-editable. */
  category: string;
  category_id: number | null;
  unit: MaterialUnit;
  current_qty: string;
  allocated_qty: string;
  reorder_threshold: string;
  avg_unit_cost: string;
  is_active: boolean;
  colour: string | null;
  /** Hex code of the reference colour when it has one — for the materials-list swatch. Null
   *  for materials still on the legacy free-text colour path. */
  colour_hex: string | null;
  material_type_id: number | null;
  material_type_name: string | null;
  barcode: string | null;
  manufacturer_id: number | null;
  manufacturer_name: string | null;
  default_supplier_id: number | null;
  default_supplier_name: string | null;
  typical_reorder_qty: string | null;
  product_url: string | null;
  image_path: string | null;
  image_original_filename: string | null;
  created_at: string;
  updated_at: string;
  on_order_qty: string | null;
  /** Time-to-stockout forecast, populated on the list and single-get paths (null on a
   *  mutation response). `weeks_of_supply` is null when there's too little sales history —
   *  `stockout_status` is then "insufficient_data"; "ok" means healthy. See lib/forecast.ts. */
  weeks_of_supply: string | null;
  consumption_rate_per_week: string | null;
  fg_buffer_weeks: string | null;
  stockout_status: StockoutStatus | null;
  abc_class: ABCClass | null;
  stock_take_interval_days: number | null;
  last_stock_take_at: string | null;
  /** Null on a mutation response; the list and single-get paths populate it. */
  classification: ResolvedClassification | null;
}

export interface Manufacturer {
  id: number;
  name: string;
  /** How many records reference this. Computed per request — see the backend's list_with_usage. */
  usage_count: number;
  website_url: string | null;
  created_at: string;
}

export interface Supplier {
  id: number;
  name: string;
  /** How many records reference this. Computed per request — see the backend's list_with_usage. */
  usage_count: number;
  website_url: string | null;
  created_at: string;
}

export interface Colour {
  id: number;
  name: string;
  /** Set when the value parses as a hex colour — the field was historically "Colour / hex". */
  hex_code: string | null;
  usage_count: number;
  created_at: string;
}

/**
 * A material category. The name is reused from the string union it replaces on purpose — every
 * place still treating a category as a string becomes a compile error, which is the cheapest
 * way to find them all.
 */
export interface MaterialCategory {
  id: number;
  name: string;
  /** Ascending. Deliberately not alphabetical — filament first, other last. */
  sort_order: number;
  /** Imposed on a material when its category is picked. Null means "leave the unit alone". */
  default_unit: MaterialUnit | null;
  /** A failed build defaults to consuming this material anyway. Was hardcoded to filament. */
  consumed_on_failed_build: boolean;
  /** Auto-added once per order rather than per unit. Was hardcoded to packaging. */
  auto_kitting_per_order: boolean;
  tracks_colour: boolean;
  tracks_material_type: boolean;
  /** Bought by the kilo, stocked by the gram, so average cost reads x1000. */
  cost_per_kg_display: boolean;
  usage_count: number;
  created_at: string;
}

export interface MaterialType {
  id: number;
  name: string;
  /** How many records reference this. Computed per request — see the backend's list_with_usage. */
  usage_count: number;
  created_at: string;
}

export interface ProductCategory {
  id: number;
  name: string;
  /** How many records reference this. Computed per request — see the backend's list_with_usage. */
  usage_count: number;
  created_at: string;
}

export type ABCClass = "A" | "B" | "C";

/**
 * An item's effective stock-take tier and cadence, with where each came from.
 *
 * `class_source`/`interval_source` are "item" (set on this item), "group" (from its
 * category or product category) or "default" (the shop-wide baseline / shipped cadence).
 * Resolved server-side deliberately — see the backend's services/abc.py, which is the only
 * place the fallback order lives.
 */
export interface ResolvedClassification {
  abc_class: ABCClass;
  interval_days: number;
  class_source: "item" | "group" | "default";
  interval_source: "item" | "tier" | "default";
  last_stock_take_at: string | null;
  next_due_at: string | null;
  /** null means never counted — a different state from "0 days late". */
  days_overdue: number | null;
  is_due: boolean;
}

export interface DueForCountItem {
  scope: "material" | "product";
  material_id: number | null;
  product_id: number | null;
  variant_id: number | null;
  name: string;
  abc_class: ABCClass;
  interval_days: number;
  last_stock_take_at: string | null;
  days_overdue: number | null;
}

export type StockTakeStatus = "open" | "closed";
export type StockTakeLineStatus =
  | "pending"
  | "counted"
  | "applied"
  | "conflict"
  | "accepted_system"
  | "skipped";

export interface StockTakeLine {
  id: number;
  material_id: number | null;
  product_id: number | null;
  variant_id: number | null;
  name: string;
  unit: string;
  /**
   * Where this line sits in the sheet: "Products" or "Materials", then the category, then
   * the parent SKU (products) or material type (materials). Resolved and ordered
   * server-side, so the sheet on screen, the CSV and the standing-variances list cannot
   * drift into three different arrangements.
   */
  section: string;
  group: string;
  subgroup: string;
  expected_qty: string;
  /** Finished stock only: how much of expected_qty is picked for open orders, and so
   * probably boxed rather than on the shelf. Null for materials. */
  allocated_qty_at_start: string | null;
  /** Null means not counted, which is a different thing from a count of zero. */
  counted_qty: string | null;
  notes: string | null;
  status: StockTakeLineStatus;
  system_qty_at_approval: string | null;
  conflict_reason: string | null;
  delta: string | null;
}

export interface StockTake {
  id: number;
  status: StockTakeStatus;
  includes_materials: boolean;
  includes_products: boolean;
  overdue_only: boolean;
  scope_description: string;
  started_at: string;
  closed_at: string | null;
  notes: string | null;
  /** Visibility only — nothing expires a take. The longer one runs the more lines land in
   * manual review, which is what this is for noticing. */
  open_days: number;
  line_count: number;
  counted_count: number;
  pending_count: number;
  conflict_count: number;
}

export interface StockTakeDetail extends StockTake {
  lines: StockTakeLine[];
}

export interface ScopeWarning {
  name: string;
  other_stock_take_id: number;
  other_started_at: string;
}

export interface StockTakeScope {
  include_materials: boolean;
  include_products: boolean;
  material_category_ids: number[];
  product_category_ids: number[];
  overdue_only: boolean;
}

export interface ScopePreview {
  candidate_count: number;
  material_count: number;
  product_count: number;
  scope_description: string;
  warnings: ScopeWarning[];
}

export interface StockTakeCreated {
  stock_take: StockTakeDetail;
  warnings: ScopeWarning[];
}

export interface ApproveResult {
  stock_take: StockTakeDetail;
  applied_count: number;
  conflict_count: number;
  skipped_count: number;
}

export interface UnresolvedVariance {
  line: StockTakeLine;
  stock_take_id: number;
  stock_take_closed_at: string | null;
}

export interface StockTakeImportResult {
  matched: number;
  skipped_blank: number;
  failed: { row: number; error: string }[];
  /** False for a dry run, and false when on_error="fail" refused the file. */
  applied: boolean;
}

export interface TierInterval {
  tier: ABCClass;
  interval_days: number;
  /** False means this is the shipped default rather than a stored value. Writing it back
   * with is_override false deletes the override, so a tier left alone keeps following the
   * defaults if they ever change. */
  is_override: boolean;
}

export interface StockCountSettings {
  default_material_abc_class: ABCClass;
  default_product_abc_class: ABCClass;
  material_tier_intervals: TierInterval[];
  product_tier_intervals: TierInterval[];
  category_tiers: { category_id: number; abc_class: ABCClass }[];
  product_category_tiers: { product_category_id: number; abc_class: ABCClass }[];
}

export type PurchaseStatus = "ordered" | "partially_received" | "received";

/** One physical arrival of part (or all) of a purchase line. */
export interface PurchaseReceipt {
  id: number;
  purchase_line_id: number;
  qty: string;
  /** null means this delivery took its pro-rata share of the line total. */
  total_cost: string | null;
  received_at: string;
  notes: string | null;
  /** One value per delivery, so a whole van-load can be undone as one thing. */
  batch_id: string | null;
}

export interface PurchaseLine {
  id: number;
  purchase_id: number;
  material_id: number;
  /** What was ordered. Never rewritten to match what turned up — see closed_at. */
  qty: string;
  total_cost: string;
  notes: string | null;
  /** Set when the rest of this line is never coming. */
  closed_at: string | null;
  received_qty: string;
  outstanding_qty: string;
  receipts: PurchaseReceipt[];
}

export interface Purchase {
  id: number;
  supplier_id: number | null;
  supplier_name: string | null;
  order_date: string;
  expected_arrival_date: string | null;
  status: PurchaseStatus;
  /** When the order was completed. null while anything is still outstanding. */
  received_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  lines: PurchaseLine[];
}

export interface MaterialStockHistoryEntry {
  id: number;
  /**
   * "purchase" is a delivery that happened; those plus "adjustment" account for the
   * material's quantity exactly. "purchase_outstanding" is what is still on order — on the
   * same timeline because that is where people look for it, but it has moved nothing.
   */
  kind: "purchase" | "purchase_outstanding" | "adjustment";
  at: string;
  qty: string;
  total_cost: string | null;
  status: PurchaseStatus | null;
  supplier_name: string | null;
  reason: string | null;
  mode: "adjust" | "set" | null;
  target_qty: string | null;
  product_id: number | null;
  product_name: string | null;
  variant_id: number | null;
  order_id: number | null;
}

export interface Product {
  id: number;
  name: string;
  sku: string | null;
  description: string | null;
  barcode: string | null;
  is_active: boolean;
  current_stock: number;
  allocated_qty: number;
  is_bundle: boolean;
  created_at: string;
  updated_at: string;
  max_buildable: number | null;
  expected_max_buildable: number | null;
  max_sellable: number | null;
  max_sellable_reason: string | null;
  expected_max_sellable: number | null;
  expected_max_sellable_reason: string | null;
  theoretical_max_sellable: number | null;
  theoretical_max_sellable_reason: string | null;
  platform_ceiling_qty: number | null;
  push_buildable_capacity: boolean;
  /** Built against an order and never held, so it is never counted. Variants follow it. */
  made_to_order: boolean;
  cost_per_unit: string | null;
  kitting_cost_per_unit: string | null;
  /**
   * Lowest and highest of the two figures above across the product's ACTIVE variants, once
   * their BOM overrides and substitutions are resolved. Both null when the product has no
   * variants, in which case the base figures are the whole story. They exist because the
   * base figures resolve the base BOM only, so for a variant product they can report a cost
   * that matches no actual variant.
   */
  cost_per_unit_min: string | null;
  cost_per_unit_max: string | null;
  kitting_cost_per_unit_min: string | null;
  kitting_cost_per_unit_max: string | null;
  /** Resolved shipping profile and its cost on the shop-wide margin fee source. Null when
   *  none is assigned — which is why such a product's orders ship with no postage cost. */
  effective_shipping_profile_id: number | null;
  effective_shipping_profile_name: string | null;
  effective_shipping_cost: string | null;
  /** Missing a shipping profile or a materials cost — see the backend's _cogs_incomplete. */
  cogs_incomplete: boolean;
  main_image_asset_id: number | null;
  ready_to_ship: number | null;
  variant_attribute1_name: string | null;
  variant_attribute2_name: string | null;
  variant_attribute3_name: string | null;
  sale_price: string | null;
  shipping_profile_id: number | null;
  platform_fee_percent: string | null;
  effective_platform_fee_percent: string | null;
  pricing_mode: PricingMode;
  pricing_variable_attribute: number | null;
  product_category_id: number | null;
  product_category_name: string | null;
  abc_class: ABCClass | null;
  stock_take_interval_days: number | null;
  last_stock_take_at: string | null;
  /** Null for a bundle (nothing to count) and on a mutation response. */
  classification: ResolvedClassification | null;
}

export interface ProductPage {
  items: Product[];
  total: number;
  /** Products with a COGS gap under the current category filter, counted whether or not the
   *  gap filter itself is on — so the toggle can show what it would reveal. */
  incomplete_total: number;
}

export type PricingMode = "product" | "variable" | "line";

export interface ProductPriceSnapshot {
  id: number;
  product_id: number;
  cost_per_unit: string;
  sale_price: string | null;
  margin_percent: string | null;
  recorded_at: string;
}

export interface MarginAlert {
  product_id: number;
  name: string;
  previous_margin_percent: string;
  current_margin_percent: string;
}

export interface BundleItem {
  component_product_id: number;
  qty: number;
}

export interface BundleItemRead extends BundleItem {
  id: number;
  bundle_product_id: number;
}

export interface Build {
  id: number;
  product_id: number;
  variant_id: number | null;
  qty_built: number;
  qty_failed: number;
  notes: string | null;
  built_at: string;
}

export interface StockAdjustment {
  id: number;
  product_id: number;
  variant_id: number | null;
  mode: "adjust" | "set";
  qty_delta: number;
  target_qty: number | null;
  reason: string;
  created_at: string;
}

export type ProductStockEventType = "build_success" | "build_failed" | "adjustment" | "order_fulfillment";

export interface ProductStockEvent {
  id: number;
  product_id: number;
  variant_id: number | null;
  event_type: ProductStockEventType;
  qty_delta: number;
  running_balance: number;
  reason: string | null;
  created_at: string;
  source_build_id: number | null;
  build_qty_built: number | null;
  build_qty_failed: number | null;
  source_adjustment_id: number | null;
  adjustment_mode: "adjust" | "set" | null;
  adjustment_target_qty: number | null;
  source_order_line_id: number | null;
  order_id: number | null;
  order_external_order_id: string | null;
}

export interface BomLine {
  material_id: number;
  qty_required: string;
}

export interface BomLineRead extends BomLine {
  id: number;
  product_id: number;
}

export interface VariantBomLine extends BomLine {
  replaces_material_id: number | null;
  line_max_buildable?: number | null;
  line_expected_max_buildable?: number | null;
}

export interface KittingBomLine {
  material_id: number;
  qty_required: string;
}

export interface KittingBomLineRead extends KittingBomLine {
  id: number;
  product_id: number;
}

export interface VariantKittingBomLine extends KittingBomLine {
  replaces_material_id: number | null;
  line_max_buildable?: number | null;
  line_expected_max_buildable?: number | null;
  unit_cost?: string | null;
}

export interface AttributeMaterialRule {
  base_material_id: number;
  value_to_material_id: Record<string, number>;
}

export interface AttributeQuantityRule {
  base_material_id: number;
  value_to_qty: Record<string, string>;
}

export interface VariantAttributeSpec {
  name: string;
  values: string[];
  material_rules?: AttributeMaterialRule[];
  quantity_rules?: AttributeQuantityRule[];
}

export interface BulkBomAmendLine {
  base_material_id: number;
  material_id?: number | null; // substitution target; null keeps the base material
  qty_required?: string | null; // null keeps the base BOM quantity
}

export interface BulkBomAmendRequest {
  attribute_name: string;
  attribute_value: string;
  lines: BulkBomAmendLine[];
  apply?: boolean;
  include_inactive?: boolean;
}

export interface BulkBomAmendChange {
  base_material_id: number;
  base_material_name: string;
  before_material_id: number | null; // null = inherited the base BOM
  before_qty: string | null;
  after_material_id: number | null; // null = the amend removes the override
  after_qty: string | null;
}

export interface BulkBomAmendUnit {
  variant_id: number;
  variant_name: string;
  changes: BulkBomAmendChange[]; // empty when already correct
}

export interface BulkBomAmendResult {
  applied: boolean;
  matched_variant_count: number;
  changed_variant_count: number;
  skipped_inactive_count: number;
  units: BulkBomAmendUnit[];
}

export interface Variant {
  id: number;
  product_id: number;
  variant_name: string;
  sku_suffix: string | null;
  is_active: boolean;
  current_stock: number;
  allocated_qty: number;
  attribute1_value: string | null;
  attribute2_value: string | null;
  attribute3_value: string | null;
  sale_price: string | null;
  shipping_profile_id: number | null;
  effective_shipping_profile_id: number | null;
  platform_fee_percent: string | null;
  effective_platform_fee_percent: string | null;
  max_buildable: number | null;
  expected_max_buildable: number | null;
  max_sellable: number | null;
  max_sellable_reason: string | null;
  expected_max_sellable: number | null;
  expected_max_sellable_reason: string | null;
  theoretical_max_sellable: number | null;
  theoretical_max_sellable_reason: string | null;
  cost_per_unit: string | null;
  kitting_cost_per_unit: string | null;
  effective_bom: VariantBomLine[];
  effective_kitting_bom: VariantKittingBomLine[];
  full_sku: string | null;
}

export interface Asset {
  id: number;
  product_id: number;
  variant_id: number | null;
  asset_type: AssetType;
  file_path: string;
  original_filename: string;
  display_order: number;
  created_at: string;
}

export interface LowStockMaterial {
  id: number;
  name: string;
  current_qty: string;
  reorder_threshold: string;
  on_order_qty: string;
  allocated_qty: string;
  supplier_id: number | null;
  supplier_name: string | null;
  consumption_rate_per_week: string | null;
  weeks_of_supply: string | null;
  fg_buffer_weeks: string | null;
  status: "critical" | "warning" | "insufficient_data";
}

export interface BuildableProduct {
  product_id: number;
  name: string;
  max_buildable: number | null;
  expected_max_buildable: number | null;
}

export interface OrderAwaitingInventory {
  line_id: number;
  order_id: number;
  product_id: number | null;
  variant_id: number | null;
  product_name: string | null;
  variant_name: string | null;
  short_by: number;
  order_placed_at: string;
}

export interface OrderAwaitingPackaging {
  order_id: number;
  material_id: number;
  material_name: string;
  short_by: string;
  order_placed_at: string;
}

export interface DashboardSummary {
  total_inventory_value: string;
  active_product_count: number;
  low_stock_materials: LowStockMaterial[];
  lowest_buildable_products: BuildableProduct[];
  margin_alerts: MarginAlert[];
  orders_awaiting_inventory: OrderAwaitingInventory[];
  orders_awaiting_packaging: OrderAwaitingPackaging[];
  /** Capped at 10 by the backend; the _total is the uncapped figure, so the section can say
   * how many it isn't showing. */
  items_due_for_count: DueForCountItem[];
  items_due_for_count_total: number;
  /** A count, not the rows: the dashboard says follow-up is outstanding and links to the
   * view that lists it. */
  unresolved_variance_count: number;
  open_stock_take: {
    id: number;
    started_at: string;
    open_days: number;
    line_count: number;
    counted_count: number;
  } | null;
}

export type ListingPlatform = "etsy" | "ebay" | "shopify";
export type OrderStatus = "pending" | "allocated" | "shipped" | "cancelled";

export interface OrderLine {
  id: number;
  order_id: number;
  product_id: number | null;
  variant_id: number | null;
  product_name: string | null;
  variant_name: string | null;
  sku: string | null;
  ordered_qty: number;
  allocated_qty: number;
  shipped_qty: number;
  unit_price: string | null;
  currency: string | null;
  external_line_id: string | null;
  needs_mapping: boolean;
  cost_per_unit_snapshot: string | null;
}

export interface Order {
  id: number;
  platform: ListingPlatform | null;
  external_order_id: string | null;
  status: OrderStatus;
  buyer_name: string | null;
  buyer_note: string | null;
  order_placed_at: string;
  shipped_at: string | null;
  cancelled_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  grand_total: string | null;
  subtotal: string | null;
  shipping_charged: string | null;
  shipping_profile_id: number | null;
  shipping_profile_name: string | null;
  shipping_cost_snapshot: string | null;
  tax_charged: string | null;
  vat_charged: string | null;
  discount_amount: string | null;
  refunded_amount: string | null;
  currency: string | null;
  payment_fees: string | null;
  payment_net: string | null;
  payment_status: string | null;
  financials_synced_at: string | null;
  // The two halves of cost of goods. materials_cogs is per line (build BOM x shipped qty,
  // frozen at first allocation); kitting_cogs is per order (what the kitting ledger actually
  // consumed, frozen at ship). Both null when there is nothing to report — render "—".
  materials_cogs: string | null;
  kitting_cogs: string | null;
  net_profit: string | null;
  cogs_pending: boolean;
  // Shipped without ever recording a postage cost, so net_profit is missing it. Distinct
  // from cogs_pending: different cause, different fix (assign the product a shipping profile).
  postage_cost_missing: boolean;
  sync_issue: string | null;
  pending_marketplace_cancellation: boolean;
  lines: OrderLine[];
}

export interface OrderPage {
  items: Order[];
  total: number;
}

export interface OrderKittingOverrideLine {
  material_id: number;
  qty_required: string;
  replaces_material_id: number | null;
}

export interface OrderKittingRequirementLine {
  material_id: number;
  material_name: string;
  auto_qty: string;
  effective_qty: string;
  reserved_qty: string;
  consumed_qty: string;
  unit_cost: string;
  unit_cost_is_frozen: boolean;
  effective_cost: string;
  consumed_cost: string;
}

export interface OrderKittingSummary {
  overrides: OrderKittingOverrideLine[];
  lines: OrderKittingRequirementLine[];
  // effective_cost_total is forward-looking (what this order will need); consumed_cost_total
  // is realised and equals Order.kitting_cogs. They converge once fully shipped.
  effective_cost_total: string;
  consumed_cost_total: string;
}

export interface ShippingProfile {
  id: number;
  name: string;
  /** Retired from the pickers, but still resolving for orders and products that use it. */
  is_archived: boolean;
  /** Products, variants and orders pointing at this. Computed per request. */
  usage_count: number;
  price: string;
  cost_etsy: string;
  cost_ebay: string;
  cost_manual: string;
  created_at: string;
  updated_at: string;
}
