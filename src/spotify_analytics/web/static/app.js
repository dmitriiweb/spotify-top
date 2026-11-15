const message = document.querySelector("#health-message");
const indicator = document.querySelector("#health-indicator");

async function updateHealth() {
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("Health check failed");

    const health = await response.json();
    if (health.status !== "ok") throw new Error("Unexpected health status");

    message.textContent = "Database connection ready";
    indicator.className = "health-indicator healthy";
    indicator.setAttribute("aria-label", "Healthy");
  } catch {
    message.textContent = "Service unavailable. Refresh to try again.";
    indicator.className = "health-indicator unavailable";
    indicator.setAttribute("aria-label", "Unavailable");
  }
}

updateHealth();
