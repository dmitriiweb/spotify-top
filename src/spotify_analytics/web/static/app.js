(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const pages = {
    overview: ["Your Music Universe", "Explore the artists, tracks, and patterns behind your saved music."],
    artists: ["Artists", "Find the people behind your saved tracks, from one-off finds to familiar names."],
    tracks: ["Tracks", "Browse, search, and sort the tracks you’ve saved."],
    timeline: ["Timeline", "See how your saved library grew, month by month or year by year."],
    discovery: ["Discovery", "Find patterns in what you saved. These insights do not measure listening activity."],
    settings: ["Settings", "Manage your Spotify connection and refresh your local library."],
  };
  const screens = Object.fromEntries(Object.keys(pages).map((id) => [id, $(`#screen-${id}`)]));
  const PAGE_SIZE = { artists: 50, tracks: 25, artistTracks: 10 };
  const PAGE_ROOT = { artists: "#artists-pagination", tracks: "#tracks-pagination", artistTracks: "#artist-tracks-pagination" };
  const state = {
    screen: "overview",
    filters: { q: "", added_from: "", added_to: "", explicit: "", album_id: "" },
    artist: null,
    offsets: { artists: 0, tracks: 0, artistTracks: 0 },
    sorts: { artists: "saved_tracks_desc", tracks: "added_at_desc", artistTracks: "added_at_desc" },
    interval: "month",
    screenRequest: null,
    timelineVersion: 0,
    distributionVersion: 0,
    artistVersion: 0,
    syncPoll: null,
    syncVersion: 0,
    localLibrary: null,
  };
  const search = $("#search-query");
  const advanced = $("#filters-form");
  const rangeForms = $$("[data-range-form]");
  const disclosures = $$(".control-disclosure");
  const monthName = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric", timeZone: "UTC" });
  const dayName = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const syncTime = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" });
  const integer = new Intl.NumberFormat();
  const decimal = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });
  const percent = new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 });
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(`--${name}`).trim();
  const palette = ["pink", "cyan", "orange", "violet", "lime", "blue", "coral", "lavender"].map(token);
  const number = (value) => integer.format(value);
  const trackCount = (value) => `${number(value)} ${value === 1 ? "track" : "tracks"}`;
  const safeDate = (value) => value ? dayName.format(new Date(value)) : "—";

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(ns, "use");
    use.setAttribute("href", `#icon-${name}`);
    svg.append(use);
    return svg;
  }

  function tile(name, tone) {
    const node = make("span", "icon-tile");
    if (tone) node.style.setProperty("--tile-tone", `var(--${tone})`);
    node.append(icon(name));
    return node;
  }

  function link(text, screen) {
    const node = make("a", "panel-link", text);
    node.href = `#screen-${screen}`;
    node.dataset.screen = screen;
    node.append(icon("arrow"));
    return node;
  }

  function button(text, action, className = "panel-link") {
    const node = make("button", className, text);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }

  function truncate(text, className = "") {
    const node = make("span", `truncate ${className}`, text);
    node.title = text;
    return node;
  }

  function duration(ms, long = false) {
    const seconds = Math.floor(ms / 1000);
    const minutes = Math.floor(seconds / 60);
    if (!long) return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
    if (minutes < 1) return `${seconds} sec`;
    const hours = Math.floor(minutes / 60);
    return hours ? `${number(hours)} h ${minutes % 60} min` : `${minutes} min`;
  }

  function httpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && !url.username && !url.password ? url : null;
    } catch { return null; }
  }

  function spotifyLink(anchor, value) {
    const url = httpsUrl(value);
    if (url?.hostname !== "open.spotify.com" || url.port) return false;
    anchor.href = url.href;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    return true;
  }

  function endpoint(path, params = {}, artistId = state.artist?.id) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries({ ...state.filters, artist_id: artistId, ...params })) {
      if (value !== "" && value != null) query.set(key, value);
    }
    return `${path}${query.size ? `?${query}` : ""}`;
  }

  async function getJSON(url, options) {
    const response = await fetch(url, { headers: { Accept: "application/json" }, ...options });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(data?.error?.message || `Request failed (${response.status}).`);
    if (!data || typeof data !== "object") throw new Error("The server returned an invalid response.");
    return data;
  }

  function read(path, params, signal, artistId) {
    return getJSON(endpoint(path, params, artistId), { signal });
  }

  function followStatusLabel() {
    const label = make("span", "artist-follow-status");
    label.hidden = true;
    return label;
  }

  async function showArtistFollowStatus(targets, signal, current = () => !signal.aborted) {
    // Local demo artists do not have Spotify IDs.
    const artists = targets.filter(([id]) => /^[A-Za-z0-9]{1,22}$/.test(id));
    if (!artists.length) return;
    const query = new URLSearchParams({ ids: artists.map(([id]) => id).join(",") });
    try {
      const status = await getJSON(`/api/artist-follow-status?${query}`, { signal });
      if (!current()) return;
      for (const [id, label] of artists) {
        const following = status[id];
        if (typeof following !== "boolean") continue;
        label.textContent = following ? "Following" : "Not following";
        label.classList.toggle("is-following", following);
        label.hidden = false;
      }
    } catch {
      // Follow status is optional. Keep the local artist data visible.
    }
  }

  function dataState(kind, message) {
    const node = make("div", `data-state ${kind}-state`);
    if (kind === "loading") {
      node.setAttribute("role", "status");
      node.append(make("span", "sr-only", message));
      for (let i = 0; i < 3; i++) {
        const line = make("span", "skeleton");
        line.setAttribute("aria-hidden", "true");
        node.append(line);
      }
    } else {
      if (kind === "error") node.setAttribute("role", "alert");
      node.append(tile(kind === "error" ? "alert" : "record"), make("p", "", message));
    }
    return node;
  }

  function displayState(root, kind, message) {
    root.replaceChildren(dataState(kind, message));
  }

  function screenFromHash() {
    const id = location.hash.replace(/^#(?:screen-)?/, "");
    return Object.hasOwn(screens, id) ? id : null;
  }

  function showScreen(id, focus = false) {
    state.screen = id;
    for (const [key, screen] of Object.entries(screens)) screen.hidden = key !== id;
    for (const nav of $$(".nav-link")) {
      if (nav.dataset.screen === id) nav.setAttribute("aria-current", "page");
      else nav.removeAttribute("aria-current");
    }
    const [title, description] = pages[id];
    $("#page-title").textContent = title;
    $("#page-description").textContent = description;
    $("#hero-record").toggleAttribute("hidden", id !== "overview");
    $("#library-toolbar").hidden = id === "settings";
    search.placeholder = id === "artists" ? "Search artists, tracks, or albums" : "Search tracks, artists, or albums";
    for (const disclosure of disclosures) disclosure.open = false;
    loadScreen();
    if (focus) {
      $("#page-title").focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
  }

  function navigate(id) {
    if (location.hash !== `#screen-${id}`) history.pushState(null, "", `#screen-${id}`);
    showScreen(id, true);
  }

  function syncFilterFields(keepSearch = false) {
    if (!keepSearch) search.value = state.filters.q;
    for (const name of ["explicit", "album_id"]) advanced.elements[name].value = state.filters[name];
    for (const form of [advanced, ...rangeForms]) {
      for (const name of ["added_from", "added_to"]) {
        form.elements[name].value = state.filters[name];
        form.elements[name].setCustomValidity("");
      }
    }
  }

  function renderChips() {
    const root = $("#active-filters");
    const labels = { q: "Search", added_from: "From", added_to: "Through", explicit: "Content", album_id: "Album", artist_id: "Artist" };
    const { explicit, ...values } = state.filters;
    const entries = Object.entries({
      ...values, explicit: explicit ? (explicit === "true" ? "Explicit only" : "Clean only") : "",
      artist_id: state.artist?.name || state.artist?.id,
    }).filter(([, value]) => value);
    const children = entries.map(([key, value], index) => {
      const node = make("span", "filter-chip");
      const remove = button("", () => {
        if (key === "artist_id") state.artist = null;
        else state.filters[key] = "";
        applyFilters();
        (root.querySelectorAll(".filter-chip button")[index] || root.querySelector(".filter-chip button") || search).focus();
      });
      remove.setAttribute("aria-label", `Remove ${labels[key].toLowerCase()} filter`);
      remove.append(icon("close"));
      node.append(truncate(`${labels[key]}: ${value}`), remove);
      return node;
    });
    if (children.length) children.push(button("Clear all", () => { clearFilters(); search.focus(); }));
    root.replaceChildren(...children);
    for (const anchor of $$("[data-export]")) anchor.href = endpoint(new URL(anchor.href).pathname);
  }

  function resetPages() {
    for (const key of Object.keys(state.offsets)) state.offsets[key] = 0;
  }

  function clearFilters() {
    for (const key of Object.keys(state.filters)) state.filters[key] = "";
    state.artist = null;
    applyFilters();
  }

  function applyFilters(keepSearch = false) {
    clearTimeout(searchTimer);
    syncFilterFields(keepSearch);
    resetPages();
    loadScreen();
  }

  function commitRange(form) {
    const from = form.elements.added_from;
    const to = form.elements.added_to;
    if (from.value && to.value && from.value > to.value) {
      to.setCustomValidity("Choose an end date on or after the start date.");
      to.reportValidity();
      return false;
    }
    state.filters.added_from = from.value;
    state.filters.added_to = to.value;
    return true;
  }

  function closeDisclosure(disclosure, restoreFocus = false) {
    disclosure.open = false;
    if (restoreFocus) $("summary", disclosure).focus();
  }

  function artwork(name, value, artist = false) {
    const node = make("span", artist ? "artwork artist-avatar" : "artwork");
    node.setAttribute("aria-hidden", "true");
    const initials = artist ? name.trim().split(/\s+/, 2).map((word) => [...word][0]).join("").toLocaleUpperCase() : "";
    node.append(initials ? make("span", "artist-initials", initials) : icon(artist ? "artists" : "record"));
    const url = httpsUrl(value);
    if (url) {
      const image = make("img");
      image.alt = "";
      image.loading = "lazy";
      image.decoding = "async";
      image.referrerPolicy = "no-referrer";
      image.addEventListener("error", () => image.remove(), { once: true });
      image.src = url.href;
      node.append(image);
    }
    return node;
  }

  function trackAction(track) {
    const node = make("a", "track-action");
    if (!spotifyLink(node, track.spotify_url)) return null;
    node.setAttribute("aria-label", `Open ${track.name} on Spotify (new tab)`);
    node.title = "Open on Spotify";
    node.append(icon("external"));
    return node;
  }

  function trackCopy(track, showAlbum = false) {
    const node = make("div", "track-copy");
    const title = make("div", "track-title-line");
    title.append(truncate(track.name, "track-title"));
    if (track.explicit) {
      const badge = make("span", "explicit-tag", "E");
      badge.title = "Explicit";
      badge.setAttribute("aria-label", "Explicit");
      title.append(badge);
    }
    const artists = track.artists.map((artist) => artist.name).join(", ") || "Unknown artist";
    const credit = showAlbum ? `${artists} · ${track.album?.name || "Unknown album"}` : artists;
    node.append(title, truncate(credit, "metadata"));
    return node;
  }

  function trackRows(root, tracks, showAlbum = false) {
    if (!tracks.length) {
      displayState(root, "empty", "No saved tracks match these filters.");
      return;
    }
    const list = make("ul", "entity-rows");
    for (const track of tracks) {
      const row = make("li", "track-row");
      const date = make("time", "track-date numeric", safeDate(track.added_at));
      date.dateTime = track.added_at;
      const meta = make("span", "track-row-meta numeric");
      meta.append(date);
      if (showAlbum) meta.append(make("span", "", duration(track.duration_ms)));
      row.append(artwork(track.album?.name, track.album?.cover_url), trackCopy(track, showAlbum), meta);
      const action = trackAction(track);
      if (action) row.append(action);
      list.append(row);
    }
    root.replaceChildren(list);
  }

  function rankedArtists(root, rows, signal) {
    if (!rows.length) {
      displayState(root, "empty", "No artists match these filters.");
      return;
    }
    const step = Math.max(1, Math.ceil(rows[0].saved_tracks / 4));
    const scale = step * 4;
    const axis = make("div", "rank-axis");
    axis.setAttribute("aria-hidden", "true");
    const ticks = make("div", "rank-ticks numeric");
    for (let i = 0; i <= 4; i++) ticks.append(make("span", "", number(i * step)));
    axis.append(make("span", "", "Artist"), ticks, make("span", "", "Tracks"));
    const list = make("ol", "rank-list");
    const followTargets = [];
    for (const { artist, saved_tracks: count } of rows) {
      const item = make("li");
      const bar = button("", () => openArtist(artist), "rank-bar");
      bar.title = `${artist.name} · ${trackCount(count)} saved`;
      const track = make("span", "rank-track");
      track.setAttribute("aria-hidden", "true");
      const fill = make("span", "rank-fill");
      fill.style.width = `${count / scale * 100}%`;
      track.append(fill);
      const label = make("span", "artist-label rank-label");
      const follow = followStatusLabel();
      label.append(make("span", "", artist.name), follow);
      followTargets.push([artist.id, follow]);
      bar.append(label, track, make("span", "rank-count numeric", number(count)), make("span", "sr-only", "saved tracks"));
      item.append(bar);
      list.append(item);
    }
    root.replaceChildren(axis, list);
    showArtistFollowStatus(followTargets, signal);
  }

  async function plot(node, traces, layout, onClick) {
    node.replaceChildren();
    if (!window.Plotly) {
      displayState(node, "error", "The local chart library did not load.");
      return;
    }
    await window.Plotly.react(node, traces, {
      autosize: true,
      paper_bgcolor: "transparent",
      plot_bgcolor: "transparent",
      font: { color: token("secondary"), size: 13, family: css.fontFamily },
      hoverlabel: { bgcolor: token("elevated"), bordercolor: token("border-hover"), font: { color: token("text"), size: 13 } },
      showlegend: false,
      ...layout,
    }, { responsive: true, displayModeBar: false, scrollZoom: false });
    node.removeAllListeners?.("plotly_click");
    if (onClick) node.on?.("plotly_click", ({ points }) => {
      if (points?.length) onClick(points[0].customdata);
    });
  }

  function resetPlot(node) {
    window.Plotly?.purge(node);
    node.replaceChildren();
  }

  async function donut(prefix, chartId, rows, action) {
    const root = $(`#${prefix}-content`);
    const status = $(`#${prefix}-state`);
    const chart = $(`#${chartId}`);
    const total = rows.reduce((sum, row) => sum + row.count, 0);
    if (!total) {
      displayState(status, "empty", "No distribution data match these filters.");
      return;
    }
    const legend = rows.map((row, index) => {
      const item = make("li");
      const key = action ? button("", () => action(row.key), "legend-key") : make("span", "legend-key");
      const swatch = make("span", "legend-swatch");
      swatch.style.setProperty("--swatch", palette[index % palette.length]);
      swatch.setAttribute("aria-hidden", "true");
      key.append(swatch, make("span", "", row.label), make("span", "legend-count numeric", number(row.count)), make("span", "legend-percent numeric", percent.format(row.count / total)));
      item.append(key);
      return item;
    });
    $(`#${prefix}-total`).textContent = number(total);
    $(`#${prefix}-legend`).replaceChildren(...legend);
    root.hidden = false;
    status.replaceChildren();
    if (!window.Plotly) {
      chart.closest("figure").hidden = true;
      return;
    }
    const entries = rows.map((row, index) => ({ ...row, color: palette[index % palette.length] })).filter((row) => row.count);
    await plot(chart, [{
      type: "pie",
      labels: entries.map((row) => row.label),
      values: entries.map((row) => row.count),
      customdata: entries.map((row) => row.key),
      hole: .7, sort: false, rotation: 0, textinfo: "none", direction: "clockwise",
      marker: { colors: entries.map((row) => row.color), line: { color: token("surface"), width: 2 } },
      hovertemplate: "%{label}<br>%{value:,} · %{percent}<extra></extra>",
    }], { margin: { t: 4, r: 4, b: 4, l: 4 } }, action);
  }

  function startDonut(prefix, chartId, message) {
    $(`#${prefix}-content`).hidden = true;
    resetPlot($(`#${chartId}`));
    displayState($(`#${prefix}-state`), "loading", message);
  }

  async function renderOverviewArtists(signal) {
    const top = $("#top-artists-chart");
    const status = $("#artist-mix-state");
    displayState(top, "loading", "Loading top artists…");
    startDonut("artist-mix", "artist-distribution-chart", "Loading artist distribution…");
    const limits = [1, 4, 9, 19, 49, Infinity];
    const labels = ["1 track", "2–4 tracks", "5–9 tracks", "10–19 tracks", "20–49 tracks", "50+ tracks"];
    const counts = labels.map(() => 0);
    // The API has no artist-count histogram. Walk the ranked directory in API-sized pages.
    const limit = 200;
    try {
      for (let offset = 0; ; offset += limit) {
        const page = await read("/api/artists", { limit, offset }, signal);
        if (signal.aborted) return;
        if (!offset) rankedArtists(top, page.items.slice(0, 10), signal);
        for (const row of page.items) counts[limits.findIndex((maximum) => row.saved_tracks <= maximum)]++;
        if (offset + limit >= page.total || !page.items.length) break;
      }
      await donut("artist-mix", "artist-distribution-chart", labels.map((label, index) => ({ label, count: counts[index] })));
    } catch (error) {
      if (signal.aborted) return;
      if ($(".loading-state", top)) displayState(top, "error", error.message);
      displayState(status, "error", error.message);
    }
  }

  async function renderOverview(signal) {
    const notice = $("#overview-error");
    const metrics = [
      ["total-tracks", (data) => number(data.total_tracks)],
      ["total-artists", (data) => number(data.total_artists)],
      ["total-albums", (data) => number(data.total_albums)],
      ["total-duration", (data) => duration(data.total_duration_ms, true)],
      ["first-saved", (data) => safeDate(data.first_saved_at)],
      ["last-saved", (data) => safeDate(data.last_saved_at)],
    ];
    notice.hidden = true;
    for (const [id] of metrics) $(`#overview-${id}`).setAttribute("aria-busy", "true");
    try {
      const data = await read("/api/overview", {}, signal);
      if (signal.aborted) return;
      for (const [id, format] of metrics) $(`#overview-${id}`).textContent = format(data);
    } catch (error) {
      if (signal.aborted) return;
      for (const [id] of metrics) $(`#overview-${id}`).textContent = "—";
      notice.hidden = false;
      displayState(notice, "error", error.message);
    } finally {
      if (!signal.aborted) for (const [id] of metrics) $(`#overview-${id}`).removeAttribute("aria-busy");
    }
  }

  async function renderRecent(signal) {
    const root = $("#recent-additions");
    displayState(root, "loading", "Loading recent additions…");
    try {
      const data = await read("/api/tracks", { limit: 5, sort: "added_at_desc" }, signal);
      if (!signal.aborted) trackRows(root, data.items);
    } catch (error) {
      if (!signal.aborted) displayState(root, "error", error.message);
    }
  }

  function openArtist(artist) {
    state.artist = artist;
    state.offsets.tracks = state.offsets.artistTracks = 0;
    if (state.screen !== "artists") {
      navigate("artists");
      return;
    }
    renderChips();
    for (const row of $$(".artist-select")) row.setAttribute("aria-pressed", String(row.dataset.artist === artist.id));
    renderArtistDetail(state.screenRequest.signal, true);
  }

  function pageControls(key, data) {
    const root = $(PAGE_ROOT[key]);
    const [previous, next] = $$("[data-page]", root);
    previous.disabled = !data || data.offset <= 0;
    next.disabled = !data || data.offset + data.limit >= data.total;
    root.hidden = !data || data.total <= data.limit;
    $("[data-page-label]", root).textContent = data?.total
      ? `${number(data.offset + 1)}–${number(Math.min(data.offset + data.limit, data.total))} of ${number(data.total)}`
      : "";
    const jump = $("[data-page-jump]", root);
    if (jump && data) {
      const lastPage = Math.max(1, Math.ceil(data.total / data.limit));
      jump.elements.page.max = lastPage;
      jump.elements.page.value = Math.floor(data.offset / data.limit) + 1;
      $("[data-page-max]", jump).textContent = `of ${number(lastPage)}`;
    }
  }

  async function renderArtists(signal) {
    const root = $("#artists-list");
    const count = $("#artists-result-count");
    pageControls("artists");
    displayState(root, "loading", "Loading the artist directory…");
    try {
      const page = await read("/api/artists", { limit: PAGE_SIZE.artists, offset: state.offsets.artists, sort: state.sorts.artists }, signal, null);
      if (signal.aborted) return;
      count.textContent = `${number(page.total)} ${page.total === 1 ? "artist" : "artists"}`;
      pageControls("artists", page);
      if (!page.items.length) {
        displayState(root, "empty", "No artists match these filters.");
        return;
      }
      const list = make("ol", "entity-rows");
      const followTargets = [];
      for (const { artist, rank, saved_tracks: tracks } of page.items) {
        const item = make("li");
        const row = button("", () => openArtist(artist), "artist-select");
        row.dataset.artist = artist.id;
        row.setAttribute("aria-pressed", String(artist.id === state.artist?.id));
        const label = make("span", "artist-label");
        const follow = followStatusLabel();
        label.append(truncate(artist.name, "artist-name"), follow);
        followTargets.push([artist.id, follow]);
        row.append(make("span", "artist-rank numeric", `#${number(rank)}`), artwork(artist.name, artist.image_url, true), label, make("span", "artist-count numeric", trackCount(tracks)));
        item.append(row);
        list.append(item);
      }
      root.replaceChildren(list);
      showArtistFollowStatus(followTargets, signal);
    } catch (error) {
      if (signal.aborted) return;
      count.textContent = "";
      displayState(root, "error", error.message);
    }
  }

  async function renderArtistDetail(signal, focus = false) {
    const detail = $("#artist-detail");
    const content = $("#artist-detail-content");
    const root = $("#artist-track-list");
    const artist = state.artist;
    const version = ++state.artistVersion;
    const current = () => !signal.aborted && version === state.artistVersion;
    detail.hidden = !artist;
    pageControls("artistTracks");
    if (!artist) {
      content.hidden = true;
      root.replaceChildren();
      return;
    }
    if (content.dataset.artist !== artist.id) content.hidden = true;
    displayState(root, "loading", "Loading the artist’s saved tracks…");
    const dates = read("/api/overview", {}, signal, artist.id).catch(() => null);
    try {
      const data = await read(`/api/artists/${encodeURIComponent(artist.id)}`, {
        limit: PAGE_SIZE.artistTracks, offset: state.offsets.artistTracks, sort: state.sorts.artistTracks,
      }, signal, null);
      if (!current()) return;
      if (state.artist.name !== data.artist.name) {
        state.artist = data.artist;
        renderChips();
      }
      const name = $("#artist-detail-name");
      name.textContent = data.artist.name;
      const follow = $("#artist-detail-follow-status");
      follow.hidden = true;
      showArtistFollowStatus([[artist.id, follow]], signal, current);
      $("#artist-detail-avatar").replaceChildren(artwork(data.artist.name, data.artist.image_url, true));
      $("#artist-detail-count").textContent = `${trackCount(data.saved_tracks)} saved${data.rank ? ` · Rank #${number(data.rank)}` : ""}`;
      $("#artist-detail-duration").textContent = duration(data.total_duration_ms, true);
      $("#artist-first-saved").textContent = "—";
      $("#artist-last-saved").textContent = "—";
      const profile = $("#artist-detail-link");
      profile.removeAttribute("href");
      profile.hidden = !spotifyLink(profile, data.artist.spotify_url);
      content.dataset.artist = artist.id;
      content.hidden = false;
      trackRows(root, data.tracks.items, true);
      pageControls("artistTracks", data.tracks);
      if (focus) name.focus();
      const overview = await dates;
      if (current() && overview) {
        $("#artist-first-saved").textContent = safeDate(overview.first_saved_at);
        $("#artist-last-saved").textContent = safeDate(overview.last_saved_at);
      }
    } catch (error) {
      if (!current()) return;
      content.hidden = true;
      displayState(root, "error", error.message);
      if (focus) { root.tabIndex = -1; root.focus(); }
    }
  }

  function tableState(body, kind, message) {
    const row = make("tr", "table-state-row");
    const cell = make("td");
    cell.colSpan = 6;
    cell.append(dataState(kind, message));
    row.append(cell);
    body.replaceChildren(row);
  }

  async function renderTracks(signal) {
    const body = $("#tracks-table-body");
    const count = $("#tracks-result-count");
    pageControls("tracks");
    tableState(body, "loading", "Loading the track library…");
    try {
      const data = await read("/api/tracks", { limit: PAGE_SIZE.tracks, offset: state.offsets.tracks, sort: state.sorts.tracks }, signal);
      if (signal.aborted) return;
      count.textContent = trackCount(data.total);
      pageControls("tracks", data);
      if (!data.items.length) {
        tableState(body, "empty", "No saved tracks match these filters.");
        return;
      }
      body.replaceChildren(...data.items.map((track, index) => {
        const row = make("tr");
        const album = track.album?.name || "Unknown album";
        const date = safeDate(track.added_at);
        const runtime = duration(track.duration_ms);
        const title = make("td");
        const identity = make("div", "track-identity");
        const copy = trackCopy(track);
        const mobile = make("div", "track-mobile-meta metadata");
        mobile.append(truncate(album), make("span", "numeric", `${date} · ${runtime}`));
        copy.append(mobile);
        identity.append(artwork(album, track.album?.cover_url), copy);
        title.append(identity);
        const albumCell = make("td");
        albumCell.append(truncate(album, "metadata"));
        const actionCell = make("td");
        const action = trackAction(track);
        if (action) actionCell.append(action);
        row.append(make("td", "metadata numeric", number(data.offset + index + 1)), title, albumCell, make("td", "metadata numeric", date), make("td", "metadata numeric", runtime), actionCell);
        return row;
      }));
    } catch (error) {
      if (signal.aborted) return;
      count.textContent = "";
      tableState(body, "error", error.message);
    }
  }

  function openPeriod([start, end]) {
    if (!state.filters.added_from || start > state.filters.added_from) state.filters.added_from = start;
    if (!state.filters.added_to || end < state.filters.added_to) state.filters.added_to = end;
    syncFilterFields();
    resetPages();
    navigate("tracks");
  }

  function timelineSummary(buckets) {
    let total = 0;
    let peak = null;
    for (const bucket of buckets) {
      total += bucket.tracks;
      if (!peak || bucket.tracks > peak.tracks) peak = bucket;
    }
    $("#timeline-total").textContent = number(total);
    $("#timeline-peak").textContent = peak ? monthName.format(new Date(peak.start)) : "—";
    $("#timeline-average").textContent = buckets.length ? decimal.format(total / buckets.length) : "—";
  }

  function renderPeriods(root, buckets, labels) {
    const select = $("select", root);
    select.replaceChildren(...buckets.map((bucket, index) => new Option(`${labels[index]}: ${trackCount(bucket.tracks)}`, index)));
    $("button", root).onclick = () => {
      const bucket = buckets[select.selectedIndex];
      if (bucket) openPeriod([bucket.start, bucket.end]);
    };
    root.hidden = false;
  }

  async function renderTimeline(signal, screen) {
    const interval = state.interval;
    const dedicated = screen === "timeline";
    const chart = $(dedicated ? "#timeline-chart" : "#saved-timeline-chart");
    const periods = $(`#${screen}-periods`);
    const version = ++state.timelineVersion;
    const current = () => !signal.aborted && version === state.timelineVersion;
    periods.hidden = true;
    resetPlot(chart);
    displayState(chart, "loading", "Loading saved-track history…");
    const data = read("/api/timeline", { interval }, signal);
    if (dedicated) {
      $("#timeline-total").textContent = $("#timeline-peak").textContent = $("#timeline-average").textContent = "—";
      const monthly = interval === "month" ? data : read("/api/timeline", { interval: "month" }, signal);
      monthly.then((result) => { if (current()) timelineSummary(result.buckets); }).catch(() => {});
    }
    try {
      const { buckets } = await data;
      if (!current()) return;
      if (!buckets.length) {
        displayState(chart, "empty", "No saved tracks in this date range.");
        return;
      }
      const yearly = interval === "year";
      const labels = buckets.map((bucket) => yearly ? bucket.start.slice(0, 4) : monthName.format(new Date(bucket.start)));
      renderPeriods(periods, buckets, labels);
      const grid = { gridcolor: token("border"), zeroline: false, fixedrange: true };
      const maximumTicks = Math.max(3, Math.floor(chart.clientWidth / 95));
      const xTicks = yearly
        ? { tickformat: "%Y", dtick: `M${12 * Math.max(1, Math.ceil(buckets.length / maximumTicks))}`, tick0: buckets[0].start }
        : { nticks: maximumTicks };
      await plot(chart, [{
        type: "scatter", mode: yearly || buckets.length < 36 ? "lines+markers" : "lines",
        x: buckets.map((bucket) => bucket.start), y: buckets.map((bucket) => bucket.tracks),
        customdata: buckets.map((bucket, index) => [bucket.start, bucket.end, labels[index]]),
        line: { width: 2.5, color: token("pink") }, marker: { size: yearly ? 8 : 5, color: token("pink-hover") },
        fill: "tozeroy", fillcolor: token("plot-fill"),
        hovertemplate: "%{customdata[2]}<br>%{y:,} saved tracks<extra></extra>",
      }], {
        margin: { t: 14, r: 15, b: 36, l: 43 },
        xaxis: { ...grid, type: "date", ...xTicks },
        yaxis: { ...grid, automargin: true, rangemode: "tozero", dtick: Math.max(1, Math.ceil(Math.max(...buckets.map((bucket) => bucket.tracks)) / 5)) },
        hovermode: "x", dragmode: false,
      }, openPeriod);
    } catch (error) {
      if (current()) displayState(chart, "error", error.message);
    }
  }

  function insight(item) {
    if (item.code === "top_artist") return {
      title: "Most saved artist", icon: "artists", tone: "pink-hover", value: trackCount(item.value),
      description: item.description, action: button("View artist", () => openArtist({ id: item.artist_id })),
    };
    if (item.code === "largest_save_month") return {
      title: "Busiest saving month", icon: "calendar", tone: "violet", value: trackCount(item.value),
      description: item.description, action: link("Explore timeline", "timeline"),
    };
    return {
      title: "Library runtime", icon: "clock", tone: "cyan", value: duration(item.value, true),
      description: "The combined runtime of the saved tracks that match these filters.",
      action: link("Browse tracks", "tracks"),
    };
  }

  async function renderInsights(signal, quick) {
    const root = $(quick ? "#quick-insights" : "#discovery-insights");
    displayState(root, "loading", "Loading library insights…");
    try {
      const data = await read("/api/insights", {}, signal);
      if (signal.aborted) return;
      const stories = data.items.filter((item) => !quick || item.code !== "library_duration").map(insight);
      if (quick && stories.length) {
        const longest = await read("/api/tracks", { limit: 1, sort: "duration_desc" }, signal).catch(() => null);
        if (signal.aborted) return;
        const track = longest?.items[0];
        if (track) {
          const action = make("a", "panel-link", "Open on Spotify");
          stories.push({
            title: "Longest saved track", icon: "clock", tone: "cyan",
            description: `${track.name} · ${duration(track.duration_ms, true)}`,
            action: spotifyLink(action, track.spotify_url) ? action : null,
          });
        }
      }
      if (!stories.length) { displayState(root, "empty", "No insights match these filters."); return; }
      root.replaceChildren(...stories.map((story, index) => {
        const card = make("article", quick ? "quick-insight" : `panel story-card${index === 0 ? " is-featured" : ""}`);
        const copy = quick ? make("div") : card;
        card.append(tile(story.icon, story.tone));
        copy.append(make("h3", "", story.title));
        if (!quick) copy.append(make("p", "story-value numeric", story.value));
        copy.append(make("p", "", story.description));
        if (story.action) copy.append(story.action);
        if (quick) card.append(copy);
        return card;
      }));
    } catch (error) {
      if (!signal.aborted) displayState(root, "error", error.message);
    }
  }

  async function renderDistribution(signal) {
    const dimension = $("#distribution-dimension").value;
    const version = ++state.distributionVersion;
    const current = () => !signal.aborted && version === state.distributionVersion;
    const explicit = dimension === "explicit";
    const status = $("#track-mix-state");
    $("#distribution-description").textContent = explicit
      ? "Select a category to filter the track library."
      : "Compare categories in your saved library. Select a category to open Tracks with your current filters.";
    startDonut("track-mix", "track-distribution-chart", "Loading track distribution…");
    try {
      const data = await read("/api/distribution", { dimension }, signal);
      if (!current()) return;
      await donut("track-mix", "track-distribution-chart", data.buckets.map((bucket) => ({
        key: bucket.key, label: bucket.label, count: bucket.tracks,
      })), (key) => {
        if (explicit) state.filters.explicit = key === "explicit" ? "true" : "false";
        syncFilterFields();
        resetPages();
        navigate("tracks");
      });
    } catch (error) {
      if (current()) displayState(status, "error", error.message);
    }
  }

  function syncMessage(message, error = false) {
    const node = $("#sync-message");
    node.textContent = message;
    node.setAttribute("role", error ? "alert" : "status");
  }

  async function renderSyncStatus() {
    const version = ++state.syncVersion;
    clearTimeout(state.syncPoll);
    const syncButton = $("#sync-button");
    const sidebar = $("#sidebar-connection-status");
    const connection = $("#connection-status-badge");
    const badge = $("#sync-status-badge");
    const progress = $("#sync-progress-region");
    try {
      const status = await getJSON("/api/sync/status");
      if (version !== state.syncVersion) return;
      const running = status.status === "running";
      const demo = status.account_id === "demo";
      const labels = { idle: "Not synced", running: "Syncing", succeeded: demo ? "Demo loaded" : "Synced", failed: "Sync failed" };
      const messages = { idle: "No sync has run yet.", running: `Processed ${trackCount(status.processed_tracks)}.`, succeeded: demo ? "This is a local demo library." : "The local library is up to date." };
      badge.textContent = labels[status.status];
      badge.className = `status-badge status-${status.status}`;
      connection.textContent = demo ? "Demo library" : status.account_id ? "Library available" : "No saved library";
      connection.className = `status-badge ${status.account_id ? "status-connected" : ""}`;
      $("#connection-status-message").textContent = demo
        ? "This library contains sample data and is not connected to Spotify."
        : status.account_id ? "A local library is available. Connect to Spotify before starting a new sync."
          : "Connect to Spotify to import your saved tracks.";
      sidebar.textContent = running ? "Sync in progress" : status.status === "failed" ? "Sync failed" : demo ? "Demo data available" : status.account_id ? "Local data available" : "No saved library";
      $("#last-sync-time").textContent = status.last_successful_sync && !demo ? syncTime.format(new Date(status.last_successful_sync)) : "Not available";
      for (const [id, value] of [["saved-tracks", status.saved_tracks], ["artists", status.artists], ["albums", status.albums]]) {
        $(`#sync-${id}`).textContent = number(value);
      }
      syncButton.disabled = running;
      syncButton.textContent = running ? "Sync in progress" : "Sync library";
      progress.hidden = !running;
      syncMessage(status.error || messages[status.status] || "The library could not be refreshed.", status.status === "failed");
      if (running) {
        state.syncPoll = setTimeout(renderSyncStatus, 1800);
      } else {
        const previous = state.localLibrary;
        state.localLibrary = { account: status.account_id, syncedAt: status.last_successful_sync };
        if (status.status === "succeeded" && previous && (previous.account !== status.account_id || previous.syncedAt !== status.last_successful_sync)) {
          if (previous.account !== status.account_id) state.artist = null;
          resetPages();
          if (state.screen !== "settings") loadScreen();
        }
      }
    } catch (error) {
      if (version !== state.syncVersion) return;
      badge.textContent = connection.textContent = sidebar.textContent = "Unavailable";
      badge.className = connection.className = "status-badge status-error";
      $("#connection-status-message").textContent = "Could not check the local library.";
      progress.hidden = true;
      syncMessage(error.message, true);
      syncButton.disabled = false;
    }
  }

  function loadScreen() {
    state.screenRequest?.abort();
    state.screenRequest = new AbortController();
    const { signal } = state.screenRequest;
    renderChips();
    switch (state.screen) {
      case "overview":
        renderOverview(signal);
        renderOverviewArtists(signal);
        renderTimeline(signal, "overview");
        renderRecent(signal);
        renderInsights(signal, true);
        break;
      case "artists":
        renderArtists(signal);
        renderArtistDetail(signal);
        break;
      case "tracks": renderTracks(signal); break;
      case "timeline": renderTimeline(signal, "timeline"); break;
      case "discovery":
        renderInsights(signal, false);
        renderDistribution(signal);
        break;
      case "settings": renderSyncStatus(); break;
    }
  }

  let searchTimer;
  function searchLibrary() {
    state.filters.q = search.value.trim();
    if (state.screen === "artists") state.artist = null;
    applyFilters(true);
  }

  document.addEventListener("click", (event) => {
    const nav = event.target.closest("a[data-screen]");
    if (nav && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
      event.preventDefault();
      if (nav.dataset.screen === "artists") state.artist = null;
      navigate(nav.dataset.screen);
    }
    for (const disclosure of disclosures) {
      if (disclosure.open && !disclosure.contains(event.target)) closeDisclosure(disclosure);
    }
  });
  window.addEventListener("hashchange", () => {
    const screen = screenFromHash() || (location.hash === "" ? "overview" : null);
    if (screen && screen !== state.screen) showScreen(screen, true);
  });
  for (const disclosure of disclosures) {
    disclosure.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && disclosure.open) {
        event.preventDefault();
        closeDisclosure(disclosure, true);
      }
    });
  }
  $("#library-search").addEventListener("submit", (event) => { event.preventDefault(); searchLibrary(); });
  search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchLibrary, 250); });
  advanced.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!commitRange(advanced)) return;
    state.filters.q = search.value.trim();
    state.filters.explicit = advanced.elements.explicit.value;
    state.filters.album_id = advanced.elements.album_id.value.trim();
    closeDisclosure(advanced.closest("details"), true);
    applyFilters();
  });
  $("#clear-filters").addEventListener("click", () => { clearFilters(); closeDisclosure(advanced.closest("details"), true); });
  for (const form of [advanced, ...rangeForms]) {
    form.addEventListener("input", () => form.elements.added_to.setCustomValidity(""));
  }
  for (const form of rangeForms) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!commitRange(form)) return;
      if (form.closest("details")) closeDisclosure(form.closest("details"), true);
      applyFilters();
    });
    $("[data-clear-range]", form).addEventListener("click", () => {
      state.filters.added_from = state.filters.added_to = "";
      if (form.closest("details")) closeDisclosure(form.closest("details"), true);
      applyFilters();
    });
  }
  for (const input of $$("[data-interval]")) input.addEventListener("change", () => {
    state.interval = input.value;
    for (const control of $$("[data-interval]")) control.checked = control.value === state.interval;
    renderTimeline(state.screenRequest.signal, state.screen);
  });
  for (const [id, key] of [["artist-sort", "artists"], ["track-sort", "tracks"], ["artist-track-sort", "artistTracks"]]) {
    $(`#${id}`).addEventListener("change", (event) => {
      state.sorts[key] = event.target.value;
      state.offsets[key] = 0;
      loadScreen();
    });
  }
  for (const [key, selector] of Object.entries(PAGE_ROOT)) {
    const root = $(selector);
    for (const control of $$("[data-page]", root)) control.addEventListener("click", () => {
      state.offsets[key] = Math.max(0, state.offsets[key] + Number(control.dataset.page) * PAGE_SIZE[key]);
      loadScreen();
    });
    const jump = $("[data-page-jump]", root);
    jump?.addEventListener("submit", (event) => {
      event.preventDefault();
      const offset = (jump.elements.page.valueAsNumber - 1) * PAGE_SIZE[key];
      if (state.offsets[key] === offset) return;
      state.offsets[key] = offset;
      loadScreen();
    });
  }
  $("#return-to-directory").addEventListener("click", () => {
    state.artist = null;
    state.offsets.artistTracks = state.offsets.tracks = 0;
    loadScreen();
    $("#artist-directory-heading").focus();
  });
  $("#distribution-dimension").addEventListener("change", () => renderDistribution(state.screenRequest.signal));
  $("#sync-button").addEventListener("click", async (event) => {
    const control = event.currentTarget;
    control.disabled = true;
    control.setAttribute("aria-busy", "true");
    $("#sync-progress-region").hidden = false;
    syncMessage("Starting library sync…");
    try {
      await getJSON("/api/sync", { method: "POST" });
      await renderSyncStatus();
    } catch (error) {
      $("#sync-progress-region").hidden = true;
      syncMessage(error.message, true);
      control.disabled = false;
    } finally { control.removeAttribute("aria-busy"); }
  });

  showScreen(screenFromHash() || "overview");
  if (state.screen !== "settings") renderSyncStatus();
})();
