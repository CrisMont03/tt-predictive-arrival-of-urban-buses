export type Station = {
  id: string; name: string; zone: string; city: string;
  latitude: number; longitude: number;
  demonstration: boolean;
  predictions: { live: boolean; demo: boolean };
};
export type Context = {
  hour: number;
  day_of_week: number;
  is_weekend: number;
  is_holiday: number | null;
  precipitation_mm: number | null;
  temp_c: number | null;
  humidity: number | null;
  traffic_density: number | null;
  env_alert_active: number | null;
};

export type Prediction = {
  query_id: string;
  station_id: string;
  waiting_time_min: number;
  confidence_interval: [number, number];
  model_version: string;
  timestamp: string;
  requested_at?: string;
  expires_at: string;
  source: 'mock' | 'cache' | 'model';
  simulation: boolean;
  context: Context;
  context_sources?: Record<string, Record<string, unknown>> | null;
};

export type HistoryEntry = Prediction & { requested_at?: string; actual_min?: number; feedback_pending?: boolean };
export type PendingFeedback = { query_id: string; actual_min: number; station_id: string };
export type Preferences = { preferred_station: string; gps_enabled: boolean; notifications_enabled: boolean };
export type Alert = {
  id: string;
  station_id: string;
  type: string;
  message: string;
  severity: 'info' | 'warning' | 'critical';
  active: boolean;
  created_at: string;
  expires_at: string;
};
export type Session = { uid: string; email: string | null; displayName?: string | null; demo: boolean };
export type DemoScenario = 'normal' | 'slow' | 'error' | 'empty';
