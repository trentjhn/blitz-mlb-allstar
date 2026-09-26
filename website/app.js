// The All-Star table: season and Show filters, search and sorting over the rows that
// build.py writes to data.js.
const { asOf, rows } = window.ALL_STARS;

// Shown where a value is missing: an unmatched Show rating, or a rate with no denominator.
const EMPTY = "-";

const state = { season: "all", show: "all", query: "", sort: "selections", descending: true };

// Case and accents fold away, so "jose ramirez" finds José Ramírez.
function fold(text) {
  return text.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

// Search also drops periods and apostrophes and reads a hyphen as a space, as the spec's name
// rule does: "st louis" finds the Cardinals, "ohearn" O'Hearn, "crow armstrong" Crow-Armstrong.
function searchable(text) {
  return fold(text).replace(/[.'\u2019]/g, "").replace(/-/g, " ").replace(/\s+/g, " ").trim();
}

function compare(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

// The opening order: most All-Star seasons first, then player name, newest season,
// batting before pitching, and team.
function defaultOrder(a, b) {
  return (
    b.all_star_selections_2024_2026 - a.all_star_selections_2024_2026 ||
    compare(fold(a.full_name), fold(b.full_name)) ||
    b.season_id - a.season_id ||
    compare(a.stat_type, b.stat_type) ||
    compare(a.team_name, b.team_name)
  );
}

// What each sortable column sorts by. A player off the Show list has no rating (null),
// and sorts last in either direction.
const SORT_KEYS = {
  selections: (row) => Number(row.all_star_selections_2024_2026),
  season: (row) => Number(row.season_id),
  player: (row) => fold(row.full_name),
  type: (row) => row.stat_type,
  team: (row) => row.team_name,
  show: (row) => (row.is_show_top100 === "true" ? Number(row.show_overall_rating) : null),
};
// Numbers start high to low; names start A to Z.
const STARTS_DESCENDING = new Set(["selections", "season", "show"]);

function matches(row) {
  const query = searchable(state.query);
  return (
    (state.season === "all" || row.season_id === state.season) &&
    (state.show === "all" || (row.is_show_top100 === "true") === (state.show === "in")) &&
    (!query ||
      [row.full_name, row.player_id, row.team_name, row.team_id].some((field) =>
        searchable(field).includes(query),
      ))
  );
}

// Sorting is stable, so rows that tie on the chosen column keep the opening order.
function ordered(list) {
  const key = SORT_KEYS[state.sort];
  const sign = state.descending ? -1 : 1;
  return [...list].sort(defaultOrder).sort((a, b) => {
    const x = key(a);
    const y = key(b);
    if (x === null || y === null) return (x === null) - (y === null);
    return sign * compare(x, y);
  });
}

function lines(first, second) {
  const cell = document.createElement("td");
  cell.append(first);
  if (second !== undefined) {
    const sub = document.createElement("span");
    sub.className = "sub";
    sub.textContent = second;
    cell.append(sub);
  }
  return cell;
}

function keyStats(row) {
  const value = (column) => row[column] || EMPTY;
  if (row.stat_type === "batting") {
    return `HR ${value("HR")} · OPS ${value("OPS")} · OPS+ ${value("OPS+")}`;
  }
  return `W ${value("W")} · ERA ${value("ERA")} · SO ${value("SO")} · WHIP ${value("WHIP")}`;
}

function tableRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.key = [row.player_id, row.season_id, row.stat_type, row.team_id].join("|");
  const name = document.createElement("strong");
  name.textContent = row.full_name;
  // Batting rows show the positions played; pitching tables have none, so the Pos cell.
  const position = row.positions_played || row.primary_position;
  const link = document.createElement("a");
  link.href = row.source_player_url;
  link.target = "_blank";
  link.rel = "noopener";
  link.textContent = "BR";
  link.setAttribute("aria-label", `Baseball Reference page for ${row.full_name}`);
  const matched = row.is_show_top100 === "true";
  tr.append(
    lines(plate(row.all_star_selections_2024_2026)),
    lines(row.season_id),
    lines(name, `${position} · ${row.bats}/${row.throws}`),
    lines(row.stat_type === "batting" ? "Batting" : "Pitching"),
    lines(row.team_name, row.team_id),
    lines(row.team_record),
    matched
      ? lines(plate(row.show_overall_rating), `#${row.show_rank} · POT ${row.show_potential_grade}`)
      : lines(EMPTY),
    lines(keyStats(row)),
    lines(link),
  );
  return tr;
}

// A summary plate: an optional label, a big number, and a unit. The text reads as one line,
// "2024: 83 rows"; the style sets the number apart.
function chip(label, number, unit) {
  const li = document.createElement("li");
  const parts = [
    label && Object.assign(document.createElement("span"), { className: "label", textContent: label }),
    number !== undefined && Object.assign(document.createElement("b"), { textContent: number }),
    unit && Object.assign(document.createElement("span"), { className: "unit", textContent: unit }),
  ].filter(Boolean);
  parts.forEach((part, i) => li.append(...(i ? [" ", part] : [part])));
  return li;
}

// A value in a bordered tile: the All-Star years and the Show rating.
function plate(text) {
  return Object.assign(document.createElement("span"), { className: "plate", textContent: text });
}

function summary() {
  const players = new Set(rows.map((row) => row.player_id));
  const onShow = new Set(rows.filter((r) => r.is_show_top100 === "true").map((r) => r.player_id));
  const perSeason = ["2024", "2025", "2026"].map((season) =>
    chip(`${season}:`, String(rows.filter((row) => row.season_id === season).length), "rows"),
  );
  document.getElementById("summary").replaceChildren(
    chip("", String(rows.length), "rows"),
    chip("", String(players.size), "unique players"),
    chip("Show top 100:", String(onShow.size), "matched"),
    ...perSeason,
    // asOf is the latest page fetch, in UTC: "2026-09-25T10:51:12Z".
    chip(`As of ${asOf.slice(0, 10)} ${asOf.slice(11, 16)} UTC`),
  );
}

function render() {
  const shown = ordered(rows.filter(matches));
  const body = document.querySelector("#all-stars tbody");
  if (shown.length) {
    body.replaceChildren(...shown.map(tableRow));
  } else {
    const empty = lines("No rows match.");
    empty.colSpan = 9;
    const tr = document.createElement("tr");
    tr.append(empty);
    body.replaceChildren(tr);
  }
  document.getElementById("showing").textContent = `Showing ${shown.length} of ${rows.length} rows`;
  for (const th of document.querySelectorAll("th[data-sort]")) {
    if (th.dataset.sort === state.sort) {
      th.setAttribute("aria-sort", state.descending ? "descending" : "ascending");
    } else {
      th.removeAttribute("aria-sort");
    }
  }
}

function pressOne(group, button) {
  for (const other of group.querySelectorAll("button")) {
    other.setAttribute("aria-pressed", String(other === button));
  }
}

document.getElementById("season-filter").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.season = button.dataset.season;
  pressOne(event.currentTarget, button);
  render();
});

document.getElementById("show-filter").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.show = button.dataset.show;
  pressOne(event.currentTarget, button);
  render();
});

document.getElementById("search").addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

for (const th of document.querySelectorAll("th[data-sort]")) {
  th.querySelector("button").addEventListener("click", () => {
    const column = th.dataset.sort;
    if (state.sort === column) {
      state.descending = !state.descending;
    } else {
      state.sort = column;
      state.descending = STARTS_DESCENDING.has(column);
    }
    render();
  });
}

summary();
render();
