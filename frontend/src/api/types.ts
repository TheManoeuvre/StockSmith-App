export type MaterialCategory = "filament" | "resin" | "pigment" | "hardware" | "packaging" | "other";
export type MaterialUnit = "g" | "ml" | "each";
export type AssetType = "main_image" | "listing_image" | "step" | "threemf" | "gcode";

export interface Material {
  id: number;
  name: string;
  category: MaterialCategory;
  unit: MaterialUnit;
  current_qty: string;
  allocated_qty: string;
  reorder_threshold: string;
  avg_unit_cost: string;
  is_active: boolean;
  colour: string | null;
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
}

export interface Manufacturer {
  id: number;
  name: string;
  website_url: string | null;
  created_at: string;
}

export interface Supplier {
  id: number;
  name: string;
  website_url: string | null;
  created_at: string;
}

export interface MaterialType {
  id: number;
  name: string;
  created_at: string;
}

export type PurchaseStatus = "ordered" | "received";

export interface PurchaseLine {
  id: number;
  purchase_id: number;
  material_id: number;
  qty: string;
  total_cost: string;
  notes: string | null;
}

export interface Purchase {
  id: number;
  supplier_id: number | null;
  supplier_name: string | null;
  order_date: string;
  status: PurchaseStatus;
  received_at: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
  lines: PurchaseLine[];
}

export interface MaterialStockHistoryEntry {
  id: number;
  kind: "purchase" | "adjustment";
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
  platform_ceiling_qty: number | null;
  cost_per_unit: string | null;
  main_image_asset_id: number | null;
  ready_to_ship: number | null;
  variant_attribute1_name: string | null;
  variant_attribute2_name: string | null;
  variant_attribute3_name: string | null;
  sale_price: string | null;
  shipping_cost: string | null;
  platform_fee_percent: string | null;
  effective_platform_fee_percent: string | null;
  pricing_mode: PricingMode;
  pricing_variable_attribute: number | null;
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
  shipping_cost: string | null;
  platform_fee_percent: string | null;
  effective_platform_fee_percent: string | null;
  max_buildable: number | null;
  expected_max_buildable: number | null;
  max_sellable: number | null;
  max_sellable_reason: string | null;
  expected_max_sellable: number | null;
  expected_max_sellable_reason: string | null;
  cost_per_unit: string | null;
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
  kitting_cost_per_unit_snapshot: string | null;
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
  tax_charged: string | null;
  vat_charged: string | null;
  discount_amount: string | null;
  refunded_amount: string | null;
  currency: string | null;
  payment_fees: string | null;
  payment_net: string | null;
  payment_status: string | null;
  financials_synced_at: string | null;
  net_profit: string | null;
  sync_issue: string | null;
  lines: OrderLine[];
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
}

export interface OrderKittingSummary {
  overrides: OrderKittingOverrideLine[];
  lines: OrderKittingRequirementLine[];
}
