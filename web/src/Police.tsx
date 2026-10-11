import { useEffect, useMemo, useState } from "react";
import { dismissIncident, fetchAlerts, fetchIncidents, fetchKpis, fetchZones, setZoneStatus, simulate, verifyIncident } from "./api";
import type { AlertItem, Incident, Kpis, ZoneCollection } from "./types";
import { ZoneMap } from "./ZoneMap";

export function Police() {
  const [zones, setZones] = useState<ZoneCollection | null>(null);
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState("KKL-BEACH-N");
  const [note, setNote] = useState("No action yet");
  const [error, setError] = useState("");

  async function load() {
    try {
      const [zoneData, kpiData, alertData, incidentData] = await Promise.all([
        fetchZones(),
        fetchKpis(),
        fetchAlerts(),
        fetchIncidents(),
      ]);
      setZones(zoneData);
      setKpis(kpiData);
      setAlerts(alertData);
      setIncidents(incidentData);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the console");
    }
  }

  useEffect(() => {
    void load();
    const socket = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?role=police`);
    socket.onmessage = () => {
      void load();
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
    () => zones?.features.find((item) => item.properties.code === selected) ?? zones?.features[0],
    [zones, selected],
  );

  async function onSimulate(action: "park" | "leave") {
    const body = await simulate(selected, action);
    const zone = body.zone;
    setNote(`${zone.name}: ${zone.occupied}/${zone.capacity} ${zone.status.replaceAll("_", " ")}`);
    await load();
  }

  async function onClose(action: "open" | "close") {
    const zone = await setZoneStatus(selected, action, action === "close" ? "Festival procession" : "Reopened");
    setNote(`${zone.name}: ${zone.status.replaceAll("_", " ")}${zone.override_reason ? ` – ${zone.override_reason}` : ""}`);
    await load();
  }

  if (error) return <p className="error">{error}</p>;
  if (!zones || !kpis || !feature) return <p className="muted">Loading enforcement console…</p>;

  return (
    <section className="police">
      <div className="kpis">
        <div><span>Capacity</span><strong>{kpis.capacity}</strong></div>
        <div><span>Occupied</span><strong>{kpis.occupied}</strong></div>
        <div><span>Free</span><strong>{kpis.free}</strong></div>
        <div><span>Open incidents</span><strong>{kpis.open_incidents}</strong></div>
      </div>
      <div className="controls">
        <label>
          Zone{" "}
          <select value={selected} onChange={(event) => setSelected(event.target.value)}>
            {zones.features.map((item) => (
              <option key={item.properties.code} value={item.properties.code}>
                {item.properties.name} ({item.properties.status.replaceAll("_", " ")})
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => void onSimulate("park")}>Simulate park</button>
        <button onClick={() => void onSimulate("leave")}>Simulate leave</button>
        <button onClick={() => void onClose("close")}>Close for festival</button>
        <button onClick={() => void onClose("open")}>Open street</button>
      </div>
      <p className="note">{note}</p>
      <div className="console">
        <aside className="panel">
          <h2>Alerts</h2>
          <p className="muted">Almost full, overflow, possible illegal</p>
          <div className="feed">
            {alerts.length === 0 ? <p className="muted">No alerts.</p> : null}
            {alerts.map((alert) => (
              <article key={alert.id} className={`incident kind-${alert.kind}`}>
                <strong>{alert.kind.replaceAll("_", " ")}</strong>
                <div>{alert.message}</div>
              </article>
            ))}
          </div>
        </aside>
        <ZoneMap features={zones.features} center={[feature.properties.lat, feature.properties.lng]} onSelect={setSelected} />
        <aside className="panel">
          <h2>Incident queue</h2>
          <div className="feed">
            {incidents.length === 0 ? <p className="muted">No open incidents.</p> : null}
            {incidents.map((incident) => (
              <article key={incident.id} className="incident">
                <div className="row">
                  <strong>#{incident.id} {incident.type.replaceAll("_", " ")}</strong>
                  <span>{incident.priority.toFixed(1)}</span>
                </div>
                <div className="muted">{incident.zone_name} · {incident.report_count} reports · {incident.cctv_status} CCTV · {incident.state}</div>
                <div className="controls">
                  <button onClick={() => void verifyIncident(incident.id).then(() => load())}>Verify</button>
                  <button onClick={() => void dismissIncident(incident.id).then(() => load())}>Dismiss</button>
                </div>
              </article>
            ))}
          </div>
        </aside>
      </div>
    </section>
  );
}
