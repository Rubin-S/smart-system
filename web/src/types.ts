export type ZoneProps = {
  id: number;
  code: string;
  name: string;
  city: string;
  kind: string;
  status: string;
  occupied: number;
  capacity: number;
  free: number;
  overflow: boolean;
  closing_soon: boolean;
  closes_at: string | null;
  minutes_until_close: number | null;
  busy_insight: string | null;
  lat: number;
  lng: number;
  near_emergency: boolean;
  override_reason: string | null;
  maps_url: string;
  updated_at: string | null;
};

export type ZoneFeature = {
  type: "Feature";
  id: string;
  geometry: { type: "Polygon"; coordinates: number[][][] };
  properties: ZoneProps;
};

export type ZoneCollection = {
  type: "FeatureCollection";
  features: ZoneFeature[];
};

export type AlertItem = {
  id: number;
  kind: string;
  zone_code: string | null;
  zone_name: string | null;
  message: string;
  level: string;
  incident_id: number | null;
};

export type Incident = {
  id: number;
  type: string;
  state: string;
  priority: number;
  report_count: number;
  cctv_status: string;
  evidence_url: string | null;
  zone_code: string | null;
  zone_name: string | null;
  vehicle_plate: string | null;
  lat: number;
  lng: number;
};

export type Kpis = {
  zones: number;
  capacity: number;
  occupied: number;
  free: number;
  open_incidents: number;
  alerts: number;
};

export const STATUS_COLOR: Record<string, string> = {
  available: "#1f8a4c",
  almost_full: "#d97706",
  full: "#dc2626",
  closed: "#64748b",
  restricted: "#6d28d9",
  unknown: "#94a3b8",
};

export function ringLatLng(feature: ZoneFeature): [number, number][] {
  return feature.geometry.coordinates[0].map(([lng, lat]) => [lat, lng]);
}

export function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}
