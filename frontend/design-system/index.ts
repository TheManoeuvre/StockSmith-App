// Barrel for the design-sync export (see docs/design-sync.md). It scopes which of the
// app's shared primitives are published to the "StockSmith UI" Claude Design project, so
// the design agent there builds with these real components. Only standalone-renderable
// pieces belong here: anything that fetches data or reads router/query context
// (NotificationCenter, SyncStatusIndicator, MaintenanceOverlay, CsvImportExport) is left out
// because it can't render in a design canvas without the app around it.
export { Badge } from "../src/components/common/Badge";
export { BarcodeLabel } from "../src/components/common/BarcodeLabel";
export { ConfirmDialog } from "../src/components/common/ConfirmDialog";
export { CopyButton } from "../src/components/common/CopyButton";
export { CreatableSelect } from "../src/components/common/CreatableSelect";
export { DetailPanel } from "../src/components/common/DetailPanel";
export { ErrorBanner } from "../src/components/common/ErrorBanner";
export { FieldRow } from "../src/components/common/FieldRow";
export { FilterTabs, type FilterTabDef } from "../src/components/common/FilterTabs";
export { Th, GroupHeaderRow } from "../src/components/common/ListTable";
export { Modal } from "../src/components/common/Modal";
export {
  DashboardIcon,
  OrdersIcon,
  ProductsIcon,
  MaterialsIcon,
  PurchasesIcon,
  StockTakeIcon,
  AlertsIcon,
  SyncIcon,
  SettingsIcon,
} from "../src/components/common/NavIcons";
export { SaveButton } from "../src/components/common/SaveButton";
export { SaveIndicator } from "../src/components/common/SaveIndicator";
export { SegmentedControl } from "../src/components/common/SegmentedControl";
export { Stat } from "../src/components/common/Stat";
export { StockCountFields } from "../src/components/common/StockCountFields";
export { Switch } from "../src/components/common/Switch";
export { Tabs, type TabDef } from "../src/components/common/Tabs";
export { UnsavedChangesDialog } from "../src/components/common/UnsavedChangesDialog";
export type { SaveStatus } from "../src/hooks/useSaveStatus";
export type { ABCClass, ResolvedClassification } from "../src/api/types";
