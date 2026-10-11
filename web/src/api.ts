import type { AlertItem, Incident, Kpis, ZoneCollection, ZoneProps } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Actor": "PC-1142",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

export function fetchZones() {
  return request<ZoneCollection>("/api/zones");
}

export function fetchKpis() {
  return request<Kpis>("/api/analytics/kpis");
}

export function fetchAlerts() {
  return request<AlertItem[]>("/api/alerts");
}

export function fetchIncidents() {
  return request<Incident[]>("/api/incidents?state=open&sort=priority");
}

export function simulate(zoneId: string, action: "park" | "leave") {
  return request<{ zone: ZoneProps; incident: Incident | null }>("/api/demo/simulate-vehicle", {
    method: "POST",
    body: JSON.stringify({ zone_id: zoneId, action }),
  });
}

export function setZoneStatus(zoneId: string, action: "open" | "close" | "restrict", reason: string) {
  return request<ZoneProps>(`/api/zones/${zoneId}/status`, {
    method: "POST",
    body: JSON.stringify({ action, reason }),
  });
}

export function verifyIncident(id: number) {
  return request<Incident>(`/api/incidents/${id}/verify`, {
    method: "POST",
    body: JSON.stringify({ note: "Verified from the console" }),
  });
}

export function dismissIncident(id: number) {
  return request<Incident>(`/api/incidents/${id}/dismiss`, {
    method: "POST",
    body: JSON.stringify({ note: "Dismissed from the console" }),
  });
}
