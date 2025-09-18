document.addEventListener("DOMContentLoaded", () => {
  const searchForm = document.getElementById("searchForm");
  const bookingInput = document.getElementById("booking_id");
  const queueLength = document.getElementById("queueLength");
  const estWait = document.getElementById("estWait");
  const completed = document.getElementById("completed");
  const currentCard = document.getElementById("currentCard");
  const completeBtnContainer = document.getElementById("completeBtnContainer");
  const liveQueue = document.getElementById("liveQueue");
  const deptFilter = document.getElementById("deptFilter");

  let currentDept = "All";

  searchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const booking_id = bookingInput.value.trim();
    if (!booking_id) return;

    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ booking_id })
    });

    if (res.ok) bookingInput.value = "";
  });

  deptFilter.addEventListener("change", () => {
    currentDept = deptFilter.value;
    // Trigger manual refresh (UI will be updated on next SSE)
    refreshUI(lastStats);
  });

  let lastStats = null;

  function refreshUI(data) {
    if (!data) return;
    lastStats = data;

    queueLength.textContent = data.queue_length;
    estWait.textContent = data.estimated_wait_min + " min";
    completed.textContent = data.completed_today;

    // --- Current patient ---
    completeBtnContainer.innerHTML = "";
    if (data.in_progress) {
      currentCard.innerHTML = `
        <strong>${data.in_progress.name || "Unknown"}</strong><br>
        Dept: ${data.in_progress.department || "General"}<br>
        ID: ${data.in_progress.booking_id}
      `;
      const btn = document.createElement("button");
      btn.className = "btn-complete";
      btn.textContent = "Mark Completed";
      btn.onclick = async () => {
        await fetch("/api/complete/" + data.in_progress.id, { method: "POST" });
      };
      completeBtnContainer.appendChild(btn);
    } else {
      currentCard.innerHTML = `<div class="muted">No one is being served right now</div>`;
    }

    // --- Queue list ---
    liveQueue.innerHTML = "";
    let filtered = data.waiting;
    if (currentDept !== "All") {
      filtered = filtered.filter(p => p.department === currentDept);
    }

    if (filtered.length === 0) {
      liveQueue.innerHTML = `<div class="muted">No patients in queue</div>`;
    } else {
      filtered.forEach((p, i) => {
        const div = document.createElement("div");
        div.className = "item";
        div.innerHTML = `
          <strong>${i + 1}. ${p.name || "Unknown"}</strong>
          <small>Dept: ${p.department || "General"} | ID: ${p.booking_id}</small>
        `;
        liveQueue.appendChild(div);
      });
    }
  }

  // --- SSE connection ---
  const evtSource = new EventSource("/stream");
  evtSource.addEventListener("update", (e) => {
    const data = JSON.parse(e.data);
    refreshUI(data);
  });
});
