import { NavLink, Route, Routes } from "react-router-dom";
import { Citizen } from "./Citizen";
import { Police } from "./Police";

export function App() {
  return (
    <>
      <header className="app-header">
        <h1>Smart parking & enforcement</h1>
        <nav>
          <NavLink to="/">Citizen map</NavLink>
          <NavLink to="/police">Police console</NavLink>
        </nav>
      </header>
      <Routes>
        <Route path="/" element={<Citizen />} />
        <Route path="/police" element={<Police />} />
      </Routes>
    </>
  );
}
