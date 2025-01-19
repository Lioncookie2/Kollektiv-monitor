document.addEventListener("DOMContentLoaded", () => {
    // --------------------------------------
    //     Variabler knyttet til dagens data
    // --------------------------------------
    let currentSort = { column: 'delay', direction: 'desc' };
    let currentData = [];
    let chartInstance = null;  // for "Forsinkelsesfordeling"
    
    // --------------------------------------
    //     Variabler og funksjoner for HISTORISK data
    // --------------------------------------
    let historyChartInstance = null;  // for historisk graf

    // Tegner historisk graf (siste X dager) i <canvas id="historyChart">
    function renderHistoryChart(historyData) {
        // historyData: [{date: '2025-01-15', total_delays: 32, average_delay: 5.7, max_delay: 18.5}, ...]

        // 1) Fjern ev. gammel graf
        if (historyChartInstance) {
            historyChartInstance.destroy();
        }

        // 2) Forbered labels og dataserier
        const labels = historyData.map(d => d.date);  // x-aksen
        const totalDelaysData = historyData.map(d => d.total_delays);
        const avgDelayData = historyData.map(d => d.average_delay);

        // 3) Canvas-kontekst
        const ctx = document.getElementById("historyChart").getContext("2d");

        // 4) Kombinasjons-graf:
        //    - Stolper for total forsinkelser
        //    - Linje for gjennomsnittsforsinkelse
        historyChartInstance = new Chart(ctx, {
            data: {
                labels: labels,
                datasets: [
                    {
                        type: "bar",
                        label: "Antall forsinkelser",
                        data: totalDelaysData,
                        backgroundColor: "rgba(54, 162, 235, 0.6)",
                        yAxisID: "y"
                    },
                    {
                        type: "line",
                        label: "Snittforsinkelse (min)",
                        data: avgDelayData,
                        borderColor: "#f59e0b",
                        fill: false,
                        tension: 0.2,
                        yAxisID: "y1"
                    }
                ]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true,
                        position: "left",
                        title: {
                            display: true,
                            text: "Antall"
                        }
                    },
                    y1: {
                        beginAtZero: true,
                        position: "right",
                        grid: {
                            drawOnChartArea: false // Ingen rutenett for høyre akse
                        },
                        title: {
                            display: true,
                            text: "Snittforsinkelse (min)"
                        }
                    }
                }
            }
        });
    }

    // Henter historiske data (siste X dager) fra /daily_stats
    function fetchHistoryData() {
        fetch("/daily_stats")
            .then(response => response.json())
            .then(data => {
                console.log("Historiske data:", data);
                renderHistoryChart(data);
            })
            .catch(error => console.error("Feil ved henting av historiske data:", error));
    }


    // --------------------------------------
    //     Hjelpefunksjoner for dagens data
    // --------------------------------------
    const getDelayClass = (minutes) => {
        if (minutes <= 5) return 'delay-low';
        if (minutes <= 15) return 'delay-medium';
        return 'delay-high';
    };

    // Formatterer forsinkelse til str: "X min, Y sek"
    const formatDelay = (delayMinutes) => {
        const minutes = Math.floor(delayMinutes);
        const seconds = Math.round((delayMinutes - minutes) * 60);
        return `${minutes} min, ${seconds} sek`;
    };

    // Kortene øverst (aktivitetsdata)
    const renderCards = (data) => {
        const totalTrips = data.length;
        const totalDelay = data.reduce((acc, item) => acc + item.delay_minutes, 0);
        const averageDelay = totalTrips > 0 ? totalDelay / totalTrips : 0;
        const punctuality = totalTrips > 0 
            ? ((totalTrips - data.filter(item => item.delay_minutes > 5).length) / totalTrips * 100).toFixed(1)
            : 100;

        document.getElementById("totalTrips").textContent = totalTrips;
        document.getElementById("averageDelay").textContent = formatDelay(averageDelay);
        document.getElementById("punctuality").textContent = `${punctuality}%`;
    };

    // Forsinkelsesfordeling (dagens data) - bar chart
    const renderGraphs = (data) => {
        // Fjern ev. eksisterende chart
        if (chartInstance) {
            chartInstance.destroy();
        }

        const delayCounts = { "0-5": 0, "6-10": 0, "11+": 0 };
        data.forEach(item => {
            if (item.delay_minutes <= 5) {
                delayCounts["0-5"]++;
            } else if (item.delay_minutes <= 10) {
                delayCounts["6-10"]++;
            } else {
                delayCounts["11+"]++;
            }
        });

        const ctx = document.getElementById("delayChart").getContext("2d");
        chartInstance = new Chart(ctx, {
            type: "bar",
            data: {
                labels: Object.keys(delayCounts),
                datasets: [{
                    label: "Antall avganger",
                    data: Object.values(delayCounts),
                    backgroundColor: ["#3474eb", "#34eb77", "#eb4034"]
                }]
            },
            options: {
                responsive: true,
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0
                        }
                    }
                }
            }
        });
    };

    // Tabell: TOPP 5 største forsinkelser (dagens)
    const renderTable = (data) => {
        const tableBody = document.getElementById("delayTable");
        tableBody.innerHTML = "";

        // Sorter data etter forsinkelse (størst først), hent kun topp 5
        const topDelays = [...data].sort((a, b) => b.delay_minutes - a.delay_minutes).slice(0, 5);

        topDelays.forEach(item => {
            const delayClass = getDelayClass(item.delay_minutes);
            const row = `
                <tr>
                    <td>${item.transport}</td>
                    <td>${item.line}</td>
                    <td>${item.station}</td>
                    <td class="${delayClass}">${formatDelay(item.delay_minutes)}</td>
                    <td>${new Date(item.timestamp).toLocaleTimeString()}</td>
                </tr>
            `;
            tableBody.innerHTML += row;
        });
    };

    // Sorterer data etter kolonne/retning
    const sortData = (data, column, direction) => {
        return [...data].sort((a, b) => {
            let compareA, compareB;
            switch(column) {
                case 'delay':
                    compareA = a.delay_minutes;
                    compareB = b.delay_minutes;
                    break;
                case 'time':
                    compareA = new Date(a.timestamp);
                    compareB = new Date(b.timestamp);
                    break;
                default:
                    compareA = (a[column] || "").toLowerCase();
                    compareB = (b[column] || "").toLowerCase();
            }
            if (direction === 'asc') {
                return compareA > compareB ? 1 : -1;
            } else {
                return compareA < compareB ? 1 : -1;
            }
        });
    };

    // --------------------------------------
    // Ny funksjon: Hente "Total forsinkelser i 2025" fra /total_2025/<transport>
    // --------------------------------------
    function fetchTotal2025(transportFilter) {
        const url = `/total_2025/${transportFilter}`;
        fetch(url)
          .then(response => response.json())
          .then(data => {
             console.log("Total 2025 data:", data);
             // Oppdater elementet med id="total2025" i dashbordet
             document.getElementById("total2025").textContent = data.total_2025;
          })
          .catch(err => console.error("Feil ved total2025:", err));
    }

    // --------------------------------------
    //         Event Listeners
    // --------------------------------------
    // Klikk på kolonne-overskrifter (sortering)
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const column = th.dataset.sort;
            
            // Oppdater retning
            if (currentSort.column === column) {
                currentSort.direction = currentSort.direction === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort.column = column;
                currentSort.direction = 'asc';
            }

            // Oppdater klasser for sorteringspiler
            document.querySelectorAll('th.sortable').forEach(header => {
                header.classList.remove('sort-asc', 'sort-desc');
            });
            th.classList.add(`sort-${currentSort.direction}`);

            // Sortér data og oppdater tabell
            currentData = sortData(currentData, column, currentSort.direction);
            renderTable(currentData);
        });
    });

    // Henter dagens data (all, bus, rail, tram)
    const fetchData = (filter = "all") => {
        fetch("/delays")
            .then(response => response.json())
            .then(data => {
                console.log("Fetched data:", data);
                // Filtrer på transport
                let filteredData = (filter === "all")
                    ? data
                    : data.filter(item => item.transport === filter);

                // Sortér i henhold til gjeldende kolonne/retning
                filteredData = sortData(filteredData, currentSort.column, currentSort.direction);

                currentData = filteredData;

                // Tegn dashbord
                renderCards(filteredData);
                renderGraphs(filteredData);
                renderTable(filteredData);
            })
            .catch(error => console.error("Error fetching data:", error));
    };

    // Klikk på transport-filter-knapper
    document.querySelectorAll(".filter-buttons button").forEach(button => {
        button.addEventListener("click", () => {
            const filter = button.dataset.filter; // "all", "bus", "rail", "tram"
            // Oppdater active-klasse
            document.querySelectorAll(".filter-buttons button").forEach(btn => {
                btn.classList.toggle("active", btn.dataset.filter === filter);
            });
            // Hent "dagens data" for det filteret
            fetchData(filter);
            // Hent total forsinkelser i 2025 for det filteret
            fetchTotal2025(filter);
        });
    });

    // --------------------------------------
    // Oppstart: hent data + historikk + total i 2025
    // --------------------------------------
    // 1) Last inn *dagens* data (default "all")
    fetchData("all");
    // 2) Hent total forsinkelser i 2025 (for "all" som default)
    fetchTotal2025("all");
    // 3) Last inn historiske data (siste 7 dager)
    fetchHistoryData();

    // 4) Oppdater dagens data hvert 30. sekund
    setInterval(() => {
        const activeFilter = document.querySelector(".filter-buttons button.active").dataset.filter;
        fetchData(activeFilter);
        // Hvis du også ønsker å oppdatere total 2025 fortløpende, kan du:
        // fetchTotal2025(activeFilter);
        // Men trolig er det ikke nødvendig hver 30. sekund, ettersom data endres kun ved midnatt
        // og manuelt klikk. Opp til deg!
    }, 30000);

    // Legg til i starten av eksisterende DOMContentLoaded
    const modal = document.getElementById("aboutModal");
    const btn = document.getElementById("aboutBtn");
    const span = document.getElementsByClassName("close-button")[0];

    btn.onclick = function() {
        modal.style.display = "block";
    }

    span.onclick = function() {
        modal.style.display = "none";
    }

    window.onclick = function(event) {
        if (event.target == modal) {
            modal.style.display = "none";
        }
    }
});
