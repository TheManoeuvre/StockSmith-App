import { api } from "./client";

export type NotificationCategory =
  | "marketplace_sync_failure"
  | "material_forecast_critical"
  | "material_forecast_warning"
  | "order_unfulfillable"
  | "pending_order_threshold"
  | "backup_failed"
  | "secondary_backup_unreachable"
  | "marketplace_api_soft_limit"
  | "marketplace_api_hard_limit"
  | "daily_summary";

export type NotificationUrgency = "immediate" | "digest";
export type NotificationDeliveryMode = "immediate" | "digest" | "off";
export type SummaryFrequency = "daily" | "weekly";

export interface Notification {
  id: number;
  category: NotificationCategory;
  urgency: NotificationUrgency;
  title: string;
  body: string;
  related_entity_type: string | null;
  related_entity_id: number | null;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPage {
  items: Notification[];
  total: number;
}

export interface UnreadCount {
  count: number;
}

export interface NotificationTypeSetting {
  alert_type: NotificationCategory;
  enabled: boolean;
  delivery_mode: NotificationDeliveryMode;
}

export interface NotificationSettings {
  windows_notifications_enabled: boolean;
  pushover_enabled: boolean;
  /** Last 4 characters only, or null if no key is stored — never the real key. */
  pushover_user_key_masked: string | null;
  quiet_hours_enabled: boolean;
  quiet_hours_start: number;
  quiet_hours_end: number;
  daily_summary_enabled: boolean;
  daily_summary_frequency: SummaryFrequency;
  daily_summary_hour_local: number;
  daily_summary_day_of_week: number | null;
  pending_order_threshold: number;
  alert_types: NotificationTypeSetting[];
}

export interface NotificationSettingsUpdate {
  windows_notifications_enabled: boolean;
  pushover_enabled: boolean;
  /** Omit (undefined) to keep the stored key unchanged, "" to clear it, or a new value to
   *  replace it — see the backend's _resolve_pushover_key. */
  pushover_user_key?: string | null;
  quiet_hours_enabled: boolean;
  quiet_hours_start: number;
  quiet_hours_end: number;
  daily_summary_enabled: boolean;
  daily_summary_frequency: SummaryFrequency;
  daily_summary_hour_local: number;
  daily_summary_day_of_week: number | null;
  pending_order_threshold: number;
  alert_types: NotificationTypeSetting[];
}

export interface PushoverTestResult {
  success: boolean;
  reason: string | null;
}

export const notificationsApi = {
  list: (params: { category?: NotificationCategory; unreadOnly?: boolean; limit?: number; offset?: number } = {}) => {
    const query = new URLSearchParams();
    if (params.category) query.set("category", params.category);
    if (params.unreadOnly) query.set("unread_only", "true");
    if (params.limit !== undefined) query.set("limit", String(params.limit));
    if (params.offset !== undefined) query.set("offset", String(params.offset));
    const qs = query.toString();
    return api.get<NotificationPage>(`/notifications${qs ? `?${qs}` : ""}`);
  },
  unreadCount: () => api.get<UnreadCount>("/notifications/unread-count"),
  markRead: (id: number) => api.post<Notification>(`/notifications/${id}/read`),
  markAllRead: () => api.post<void>("/notifications/read-all"),
  getSettings: () => api.get<NotificationSettings>("/settings/notifications"),
  updateSettings: (settings: NotificationSettingsUpdate) =>
    api.put<NotificationSettings>("/settings/notifications", settings),
  testPushover: () => api.post<PushoverTestResult>("/settings/notifications/pushover/test"),
  clearPushover: () => api.delete<void>("/settings/notifications/pushover"),
};
