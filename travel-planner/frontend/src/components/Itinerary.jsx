const money = (value) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    value,
  );
const ICONS = { transport: "↗", dining: "◒", activity: "✦" };

export default function Itinerary({ itinerary, budget }) {
  let plan = itinerary;
  if (typeof plan === "string") {
    try {
      plan = JSON.parse(plan);
    } catch {
      return (
        <section className="card itinerary">
          <h2>Your day plan</h2>
          <pre className="raw-plan">{plan}</pre>
        </section>
      );
    }
  }
  if (!plan || typeof plan !== "object")
    return (
      <section className="card itinerary">
        <p>The planner returned no itinerary. Try planning again.</p>
      </section>
    );
  const items = Array.isArray(plan.items)
    ? plan.items.filter((item) => item && typeof item === "object")
    : [];
  const total = items.reduce(
    (sum, item) =>
      sum + (Number.isFinite(Number(item.cost)) ? Number(item.cost) : 0),
    0,
  );
  return (
    <section className="card itinerary">
      <div className="itinerary-heading">
        <div>
          <span className="eyebrow">YOUR DAY, CONNECTED</span>
          <h2>A day in {String(plan.city || "your city")}</h2>
          <p>
            {String(
              plan.summary || "Your agents have put together this itinerary.",
            )}
          </p>
        </div>
        <div className="total-cost">
          <span>Estimated total</span>
          <strong>{money(total)}</strong>
          <span>of {money(budget)} budget</span>
        </div>
      </div>
      {total > budget && (
        <p className="budget-warning" role="status">
          This plan is {money(total - budget)} above your budget
          {total <= budget * 1.1
            ? ", within the demo’s 10% negotiation tolerance"
            : " after budget revisions"}
          .
        </p>
      )}
      {items.length ? (
        <ol className="timeline">
          {items.map((item, index) => (
            <li
              key={index}
              className={`timeline-item category-${item.category}`}
            >
              <time>{String(item.time || "—")}</time>
              <span className="timeline-icon">
                {ICONS[item.category] || "✦"}
              </span>
              <div>
                <span className="eyebrow">
                  {String(item.category || "activity")}
                </span>
                <h3>{String(item.title || "Suggested stop")}</h3>
                <p>{String(item.description || "")}</p>
              </div>
              <strong>{money(Number(item.cost) || 0)}</strong>
            </li>
          ))}
        </ol>
      ) : (
        <pre className="raw-plan">{JSON.stringify(plan, null, 2)}</pre>
      )}
    </section>
  );
}
