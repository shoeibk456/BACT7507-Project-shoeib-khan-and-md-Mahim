// Bar chart: available vs borrowed copies for each category
function renderCategoryChart(labels, available, borrowed) {
    var canvas = document.getElementById("categoryChart");
    if (!canvas) { return; }
    new Chart(canvas, {
        type: "bar",
        data: {
            labels: labels,
            datasets: [
                { label: "Available", data: available, backgroundColor: "#1a5fd0" },
                { label: "Borrowed", data: borrowed, backgroundColor: "#6b3fa0" }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "bottom" } },
            scales: { y: { beginAtZero: true, ticks: { precision: 0 } } }
        }
    });
}

// Doughnut chart: how many loans are Borrowed / Returned / Overdue
function renderStatusChart(counts) {
    var canvas = document.getElementById("statusChart");
    if (!canvas) { return; }
    new Chart(canvas, {
        type: "doughnut",
        data: {
            labels: ["Borrowed", "Returned", "Overdue"],
            datasets: [{ data: counts, backgroundColor: ["#1a5fd0", "#1fa971", "#e6483a"] }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { position: "bottom" } }
        }
    });
}
