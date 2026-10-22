import { useState } from "react";

export default function InputForm({ onSubmit, loading }) {
  const [city, setCity] = useState("Tokyo");
  const [interests, setInterests] = useState("street food, history, temples");
  const [budget, setBudget] = useState(150);

  function handleSubmit(e) {
    e.preventDefault();
    onSubmit({
      city: city.trim(),
      interests: interests
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean),
      budget: Number(budget),
    });
  }

  return (
    <form className="card input-form" onSubmit={handleSubmit}>
      <h2>Trip details</h2>

      <label>
        City
        <input
          value={city}
          onChange={(e) => setCity(e.target.value)}
          placeholder="e.g. Lisbon"
          maxLength={100}
          pattern=".*\S.*"
          required
          disabled={loading}
        />
      </label>

      <label>
        Interests
        <input
          value={interests}
          onChange={(e) => setInterests(e.target.value)}
          placeholder="comma-separated"
          maxLength={500}
          disabled={loading}
        />
        <span className="hint">Comma-separated</span>
      </label>

      <label>
        Budget (USD)
        <input
          type="number"
          min="1"
          max="1000000"
          step="0.01"
          disabled={loading}
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
          required
        />
      </label>

      <button type="submit" disabled={loading}>
        {loading ? "Agents working..." : "Plan My Day!"}
      </button>
    </form>
  );
}
