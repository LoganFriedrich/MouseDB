// Datasette lists every column's description above the table. On a table with
// sixty columns that list fills the screen and the rows start far below it, so
// it is folded behind a "What each column means" toggle (open it to read).
// The same descriptions also appear in each column heading's menu.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("dl.column-descriptions").forEach(function (dl) {
    var details = document.createElement("details");
    var summary = document.createElement("summary");
    var n = dl.querySelectorAll("dt").length;
    summary.textContent = "What each column means (" + n + " columns) -- click to open";
    summary.style.cursor = "pointer";
    summary.style.fontWeight = "bold";
    dl.parentNode.insertBefore(details, dl);
    details.appendChild(summary);
    details.appendChild(dl);
  });
});
