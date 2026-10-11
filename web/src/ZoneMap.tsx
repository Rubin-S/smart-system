import { useEffect } from "react";
import { MapContainer, Polygon, TileLayer, Tooltip, useMap } from "react-leaflet";
import type { ZoneFeature } from "./types";
import { STATUS_COLOR, ringLatLng, statusLabel } from "./types";

type Props = {
  features: ZoneFeature[];
  center: [number, number];
  onSelect: (code: string) => void;
};

function Recenter({ center }: { center: [number, number] }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, map.getZoom());
  }, [center, map]);
  return null;
}

export function ZoneMap({ features, center, onSelect }: Props) {
  return (
    <div className="map-pane">
      <MapContainer center={center} zoom={14} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
        <Recenter center={center} />
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {features.map((feature) => {
          const status = feature.properties.status;
          const color = STATUS_COLOR[status] ?? "#64748b";
          return (
            <Polygon
              key={feature.properties.code}
              positions={ringLatLng(feature)}
              pathOptions={{ color, weight: 2, fillColor: color, fillOpacity: 0.45 }}
              eventHandlers={{ click: () => onSelect(feature.properties.code) }}
            >
              <Tooltip sticky>
                {feature.properties.name}: {statusLabel(status)}, {feature.properties.free} free
              </Tooltip>
            </Polygon>
          );
        })}
      </MapContainer>
    </div>
  );
}
