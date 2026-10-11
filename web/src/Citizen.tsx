import { useEffect, useMemo, useState } from "react";
import { fetchZones } from "./api";
import type { ZoneCollection, ZoneFeature } from "./types";
import { STATUS_COLOR, statusLabel } from "./types";
import { ZoneMap } from "./ZoneMap";

export function Citizen() {
  const [data, setData] = useState<ZoneCollection | null>(null);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState("KKL-BEACH-N");

  async function load() {
    try {
      const zones = await fetchZones();
      setData(zones);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load zones");
    }
  }

  useEffect(() => {
    void load();
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?role=citizen`);
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as { type: string; data?: { code?: string } };
      if (message.type === "zone.updated" && message.data?.code) {
        setData((current) => {
          if (!current) return current;
          return {
            ...current,
            features: current.features.map((feature) =>
              feature.properties.code === message.data?.code
                ? { ...feature, properties: { ...feature.properties, ...message.data } }
                : feature,
            ),
          };
        });
      } else {
        void load();
      }
    };
    const ping = window.setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping" }));
    }, 20000);
    return () => {
      window.clearInterval(ping);
      socket.close();
    };
  }, []);

  const feature = useMemo(
    () => data?.features.find((item) => item.properties.code === selected) ?? data?.features[0],
    [data, selected],
  );

  if (error) return <p className="error">{error}</p>;
  if (!data || !feature) return <p className="muted">Loading parking map…</p>;

  return (
    <section className="layout">
      <aside className="panel">
        <h2>Nearby streets</h2>
        <ul className="zone-list">
          {data.features.map((item) => (
            <li key={item.properties.code}>
              <ZoneButton feature={item} active={item.properties.code === feature.properties.code} onSelect={setSelected} />
            </li>
          ))}
        </ul>
        <ZoneDetail feature={feature} />
      </aside>
      <ZoneMap features={data.features} center={[feature.properties.lat, feature.properties.lng]} onSelect={setSelected} />
    </section>
  );
}

function ZoneButton({ feature, active, onSelect }: { feature: ZoneFeature; active: boolean; onSelect: (code: string) => void }) {
  const zone = feature.properties;
  return (
    <button className={active ? "active" : ""} onClick={() => onSelect(zone.code)}>
      <div className="row">
        <span className="name">{zone.name}</span>
        <span className="badge" style={{ background: STATUS_COLOR[zone.status] ?? "#64748b" }}>{statusLabel(zone.status)}</span>
      </div>
      <div className="muted">{zone.city} · {zone.free} free of {zone.capacity}</div>
    </button>
  );
}

function ZoneDetail({ feature }: { feature: ZoneFeature }) {
  const zone = feature.properties;
  return (
    <div className="detail">
      <h2>{zone.name}</h2>
      <p className="note">{statusLabel(zone.status)} · {zone.occupied}/{zone.capacity} · {zone.free} free</p>
      {zone.busy_insight ? <p>{zone.busy_insight}</p> : null}
      {zone.closing_soon ? <p className="banner">Closes in {zone.minutes_until_close} min</p> : null}
      {zone.override_reason ? <p className="banner">Closed – {zone.override_reason}</p> : null}
      <a className="nav-link" href={zone.maps_url}>Navigate</a>
    </div>
  );
}
