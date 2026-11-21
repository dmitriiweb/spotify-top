(() => {
  "use strict";

  const screenIds = ["overview", "artists", "tracks", "timeline", "discovery", "settings"];
  const screens = Object.fromEntries(screenIds.map((id) => [id, document.querySelector(`#screen-${id}`)]));
  const filtersForm = document.querySelector("#filters-form");
  const filters = {
    added_from: document.querySelector("#date-from"),
    added_to: document.querySelector("#date-to"),
    q: document.querySelector("#search-query"),
  };
  const chipRoot = document.querySelector("#active-filters");
  const state = { screen: "overview", artistId: null, artistName: "", offsets: { artists: 0, tracks: 0, artistTracks: 0 }, sorts: { artists: "saved_tracks_desc", tracks: "added_at_desc", artistTracks: "added_at_desc" }, interval: "month", dimension: "release_decade", dimensionRequest: 0, request: 0, syncPoll: null, localLibrary: null };
  const $ = (selector, root = document) => root.querySelector(selector);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  };
  const safeDate = (value) => value ? new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "—";
  const safeDateTime = (value) => value ? new Date(value).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC" }) : "—";
  function httpsUrl(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" ? url.href : null;
    } catch { return null; }
  }
  function spotifyHref(value) {
    try {
      const url = new URL(value);
      return url.protocol === "https:" && url.hostname === "open.spotify.com" && !url.port && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  const duration = (ms) => {
    if (!Number.isFinite(ms)) return "—";
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.floor((ms % 60000) / 1000);
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  };
  const endpoint = (path, params = {}, { includeArtist = true } = {}) => {
    const query = new URLSearchParams();
    const activeFilters = currentFilters();
    if (!includeArtist) delete activeFilters.artist_id;
    Object.entries(activeFilters).forEach(([key, value]) => { if (value !== "") query.set(key, value); });
    Object.entries(params).forEach(([key, value]) => { if (value !== null && value !== undefined && value !== "") query.set(key, String(value)); });
    return `${path}${query.size ? `?${query}` : ""}`;
  };
  function currentFilters() {
    return {
      added_from: filters.added_from?.value.trim() || "",
      added_to: filters.added_to?.value.trim() || "",
      q: filters.q?.value.trim() || "",
      explicit: document.querySelector('[name="explicit"]')?.value || "",
      album_id: document.querySelector('[name="album_id"]')?.value.trim() || "",
      ...(state.artistId ? { artist_id: state.artistId } : {}),
    };
  }
  async function getJSON(url, options) {
    const response = await fetch(url, { headers: { Accept: "application/json" }, ...options });
    let data;
    try { data = await response.json(); } catch { data = null; }
    if (!response.ok) {
      const message = data?.error?.message || `Request failed (${response.status})`;
      throw new Error(message);
    }
    if (!data || typeof data !== "object") throw new Error("The server returned an invalid response.");
    return data;
  }
  function setContent(root, node) {
    if (!root) return;
    root.replaceChildren(node);
  }
  function loading(root, label = "Loading library data…") {
    if (!root) return;
    const box = make("p", "data-state loading-state", label);
    box.setAttribute("role", "status");
    setContent(root, box);
  }
  function showError(root, error) {
    if (!root) return;
    const box = make("p", "data-state error-state", error?.message || "Unable to load data.");
    box.setAttribute("role", "alert");
    setContent(root, box);
  }
  function empty(root, message = "No matching library data.") {
    if (!root) return;
    setContent(root, make("p", "data-state empty-state", message));
  }
  function contentRoot(screen, key) {
    if (!screen) return null;
    const existing = $(`[data-dashboard-content="${key}"]`, screen);
    if (existing) return existing;
    const root = make("div", "dashboard-content");
    root.dataset.dashboardContent = key;
    screen.append(root);
    return root;
  }
  function renderNav() {
    document.querySelectorAll("[data-screen]").forEach((link) => {
      const active = link.dataset.screen === state.screen;
      link.classList.toggle("active", active);
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    Object.entries(screens).forEach(([id, screen]) => {
      if (!screen) return;
      const visible = id === state.screen;
      screen.hidden = !visible;
      screen.classList.toggle("is-active", visible);
      screen.setAttribute("aria-hidden", String(!visible));
    });
  }
  function navigate(screen) {
    if (!screens[screen]) return;
    state.screen = screen;
    renderNav();
    history.replaceState(null, "", `#screen-${screen}`);
    loadScreen();
  }
  function renderChips() {
    if (!chipRoot) return;
    chipRoot.replaceChildren();
    const explicit = document.querySelector('[name="explicit"]');
    const album = document.querySelector('[name="album_id"]');
    const labels = [
      ["added_from", "From", filters.added_from?.value],
      ["added_to", "To", filters.added_to?.value],
      ["q", "Search", filters.q?.value],
      ["explicit", "Content", explicit?.value ? (explicit.value === "true" ? "Explicit" : "Clean") : ""],
      ["album_id", "Album", album?.value],
      ["artist_id", "Artist", state.artistName || state.artistId],
    ];
    let count = 0;
    labels.forEach(([key, label, value]) => {
      if (!value) return;
      count += 1;
      const chip = make("span", "filter-chip");
      chip.append(make("span", "", `${label}: ${value}`));
      const remove = make("button", "chip-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${label.toLowerCase()} filter`);
      remove.addEventListener("click", () => {
        if (key === "artist_id") clearArtist();
        else if (key === "explicit" && explicit) explicit.value = "";
        else if (key === "album_id" && album) album.value = "";
        else if (filters[key]) {
          filters[key].value = "";
          if (key === "q") {
            const trackSearch = document.querySelector("#track-search");
            const artistSearch = document.querySelector("#artist-search");
            if (trackSearch) trackSearch.value = "";
            if (artistSearch) artistSearch.value = "";
            if (filters.q?._debounce) clearTimeout(filters.q._debounce);
          }
        }
        applyFilters();
      });
      chip.append(remove); chipRoot.append(chip);
    });
    if (!count) chipRoot.append(make("span", "filter-empty", "No filters applied"));
    else {
      const clear = make("button", "clear-filters", "Clear all");
      clear.type = "button"; clear.addEventListener("click", clearFilters); chipRoot.append(clear);
    }
    refreshExportLinks();
  }
  function refreshExportLinks() {
    document.querySelectorAll("[data-export]").forEach((link) => {
      const kind = link.dataset.export;
      const path = kind === "artists" ? "/api/export/artists.csv" : kind === "tracks" ? "/api/export/tracks.csv" : "/api/export/library.json";
      link.href = endpoint(path);
    });
  }

  function clearArtist() {
    state.artistId = null;
    state.artistName = "";
    state.offsets.tracks = 0;
    state.offsets.artistTracks = 0;
  }
  function resetPages() {
    state.offsets.artists = 0;
    state.offsets.tracks = 0;
    state.offsets.artistTracks = 0;
  }
  function applyFilters() {
    resetPages();
    loadScreen();
  }

  function clearFilters() {
    Object.values(filters).forEach((input) => { if (input) input.value = ""; });
    if (filters.q?._debounce) clearTimeout(filters.q._debounce);
    const explicit = document.querySelector('[name="explicit"]');
    if (explicit) explicit.value = "";
    const album = document.querySelector('[name="album_id"]');
    if (album) album.value = "";
    const trackSearch = document.querySelector("#track-search");
    if (trackSearch) trackSearch.value = "";
    const artistSearch = document.querySelector("#artist-search");
    if (artistSearch) artistSearch.value = "";
    clearArtist();
    applyFilters();
  }
  function number(value) { return Number(value || 0).toLocaleString(); }
  async function renderOverview(requestId) {
    const root = contentRoot(screens.overview, "overview");
    loading(root);
    renderRecentAdditions(requestId);
    renderOverviewInsights(requestId);
    try {
      const overview = await getJSON(endpoint("/api/overview"));
      if (requestId !== state.request) return;
      const values = {
        "overview-total-tracks": number(overview.total_tracks),
        "overview-total-artists": number(overview.total_artists),
        "overview-total-albums": number(overview.total_albums),
        "overview-total-duration": duration(overview.total_duration_ms),
        "overview-first-saved": safeDate(overview.first_saved_at),
        "overview-last-saved": safeDate(overview.last_saved_at),
      };
      Object.entries(values).forEach(([id, value]) => { const node = document.getElementById(id); if (node) node.textContent = value; });
      root.remove();
      renderTopArtistsChart(requestId);
      renderDistributionChart(requestId);
      renderTimelineChart("saved-timeline-chart", requestId);
    } catch (error) {
      if (requestId !== state.request) return;
      showError(root, error);
      ["overview-total-tracks", "overview-total-artists", "overview-total-albums", "overview-total-duration", "overview-first-saved", "overview-last-saved"].forEach((id) => {
        const node = document.getElementById(id); if (node) node.textContent = "—";
      });
      [ "top-artists-chart", "artist-distribution-chart", "saved-timeline-chart" ].forEach((id) => {
        const node = chartNode(id); if (node) node.textContent = `Unavailable: ${error.message}`;
      });
    }
  }
  async function renderRecentAdditions(requestId) {
    const root = document.querySelector("#recent-additions");
    if (!root) return;
    loading(root, "Loading recent additions…");
    try {
      const data = await getJSON(endpoint("/api/tracks", { limit: 5, sort: "added_at_desc" }));
      if (requestId !== state.request) return;
      root.replaceChildren();
      if (!data.items?.length) { empty(root, "No saved tracks match these filters."); return; }
      const list = make("ol", "recent-additions-list");
      data.items.forEach((track) => {
        const row = make("li", "recent-addition");
        const href = spotifyHref(track.spotify_url);
        const title = href ? make("a", "table-link", track.name) : make("span", "", track.name);
        if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; }
        row.append(title, make("span", "", `${track.artists.map((artist) => artist.name).join(", ")} · ${safeDate(track.added_at)}`));
        list.append(row);
      });
      root.append(list);
    } catch (error) { if (requestId === state.request) showError(root, error); }
  }
  async function renderOverviewInsights(requestId) {
    const root = document.querySelector("#quick-insights");
    if (!root) return;
    loading(root, "Loading quick insights…");
    try {
      const data = await getJSON(endpoint("/api/insights"));
      if (requestId !== state.request) return;
      root.replaceChildren();
      if (!data.items?.length) { empty(root, "No insights are available for these filters."); return; }
      data.items.forEach((item) => {
        const card = make("article", "insight-card");
        card.append(make("h3", "", item.title), make("p", "", item.description));
        if (item.code === "library_duration") card.append(make("span", "insight-value", duration(item.value)));
        else card.append(make("span", "insight-value", number(item.value)));
        if (item.artist_id) {
          const action = make("button", "text-button", "View artist");
          action.type = "button"; action.addEventListener("click", () => openArtist(item.artist_id));
          card.append(action);
        }
        root.append(card);
      });
    } catch (error) { if (requestId === state.request) showError(root, error); }
  }
  function chartNode(id) { return document.getElementById(id); }
  function clearChart(node, message) {
    if (!node) return;
    if (typeof window.Plotly?.purge === "function") window.Plotly.purge(node);
    node.textContent = message;
  }
  function plot(id, data, layout, config = {}) {
    const node = chartNode(id);
    if (!node) return;
    if (!window.Plotly) { clearChart(node, "Charts are unavailable because the local chart library did not load."); return; }
    node.replaceChildren();
    window.Plotly.react(node, data, { autosize: true, margin: { t: 18, r: 12, b: 48, l: 42 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { color: "#dce8df" }, ...layout }, { responsive: true, displayModeBar: false, ...config });
  }
  async function renderTopArtistsChart(requestId = state.request) {
    const node = chartNode("top-artists-chart");
    if (!node) return;
    clearChart(node, "Loading artists…");
    try {
      const data = await getJSON(endpoint("/api/artists", { limit: 8, offset: 0, sort: "saved_tracks_desc" }));
      if (requestId !== state.request) return;
      const rows = data.items || [];
      if (!rows.length) { clearChart(node, "No artists match these filters."); return; }
      const positions = rows.map((_, index) => index);
      plot("top-artists-chart", [{ type: "bar", orientation: "h", x: rows.map((row) => row.saved_tracks), y: positions, customdata: rows.map((row) => [row.artist.id, row.artist.name]), marker: { color: "#8b5cf6" }, hovertemplate: "%{customdata[1]}: %{x} saved tracks<extra></extra>" }], { yaxis: { automargin: true, autorange: "reversed", tickmode: "array", tickvals: positions, ticktext: rows.map((row) => row.artist.name) }, xaxis: { title: "Saved tracks", rangemode: "tozero" }, showlegend: false });
      node.removeAllListeners?.("plotly_click");
      node.on?.("plotly_click", (event) => {
        const [id, name] = event?.points?.[0]?.customdata || [];
        if (id) openArtist(id, name);
      });
    } catch (error) { if (requestId === state.request) clearChart(node, `Artists chart unavailable: ${error.message}`); }
  }
  async function renderDistributionChart(requestId = state.request) {
    const node = chartNode("artist-distribution-chart");
    if (!node) return;
    const dimension = state.dimension;
    const sequence = ++state.dimensionRequest;
    clearChart(node, "Loading distribution…");
    try {
      const data = await getJSON(endpoint("/api/distribution", { dimension }));
      if (requestId !== state.request || sequence !== state.dimensionRequest || dimension !== state.dimension) return;
      const rows = data.buckets || [];
      if (!rows.length) { clearChart(node, "No distribution data for these filters."); return; }
      plot("artist-distribution-chart", [{ type: "pie", labels: rows.map((row) => row.label), values: rows.map((row) => row.tracks), customdata: rows.map((row) => row.key), hole: 0.58, textinfo: "label+percent", marker: { colors: ["#6d28d9", "#7c3aed", "#8b5cf6", "#a78bfa", "#c4b5fd"] }, hovertemplate: "%{label}: %{value} tracks<extra></extra>" }], { showlegend: true, legend: { orientation: "h", y: -0.2 } });
      node.removeAllListeners?.("plotly_click");
      node.on?.("plotly_click", (event) => {
        const customdata = event?.points?.[0]?.customdata;
        const key = Array.isArray(customdata) ? customdata[0] : customdata;
        if (dimension === "explicit" && key) {
          const control = document.querySelector('[name="explicit"]');
          if (control) control.value = key === "explicit" ? "true" : "false";
          resetPages();
        }
        state.offsets.tracks = 0;
        navigate("tracks");
      });
    } catch (error) { if (requestId === state.request && sequence === state.dimensionRequest) clearChart(node, `Distribution chart unavailable: ${error.message}`); }
  }
  async function renderArtists(requestId) {
    if (!state.artistId) {
      document.querySelector("#artist-detail-heading").textContent = "Choose an artist";
      const detail = document.querySelector("#artist-detail-content");
      const emptyMessage = document.querySelector("#artist-detail-empty");
      if (detail) detail.hidden = true;
      if (emptyMessage) emptyMessage.hidden = false;
    }
    const root = document.querySelector("#artists-list") || contentRoot(screens.artists, "artists");
    loading(root);
    try {
      const limit = 25; const data = await getJSON(endpoint("/api/artists", { limit, offset: state.offsets.artists, sort: state.sorts.artists }, { includeArtist: false }));
      if (requestId !== state.request) return;
      const items = data.items || [];
      root.replaceChildren();
      const count = document.querySelector("#artists-result-count");
      if (count) count.textContent = `${number(data.total)} artists`;
      if (!items.length) empty(root, "No artists match these filters.");
      else {
        const list = make("ol", "artist-list");
        items.forEach((item) => {
          const card = make("li", "artist-row");
          const button = make("button", "artist-select", item.artist.name); button.type = "button";
          button.addEventListener("click", () => openArtist(item.artist.id, item.artist.name));
          card.append(make("span", "artist-rank", `#${item.rank}`), button, make("span", "artist-count", `${number(item.saved_tracks)} tracks`));
          list.append(card);
        });
        root.append(list);
      }
      const label = document.querySelector("#artists-page-label");
      if (label) label.textContent = data.total ? `Page ${Math.floor(data.offset / data.limit) + 1} of ${Math.max(1, Math.ceil(data.total / data.limit))}` : "Page 0";
      const previous = document.querySelector("#artists-previous");
      const next = document.querySelector("#artists-next");
      if (previous) previous.disabled = data.offset <= 0;
      if (next) next.disabled = data.offset + data.limit >= data.total;
    } catch (error) { if (requestId === state.request) showError(root, error); }
  }
  async function renderTracks(requestId, artistId = null) {
    const limit = 25;
    if (!artistId) {
      const body = document.querySelector("#tracks-table-body");
      if (body) {
        const row = make("tr"); const cell = make("td", "table-state", "Loading saved tracks…");
        cell.colSpan = 5; row.append(cell); body.replaceChildren(row);
      }
    }
    try {
      let data;
      if (artistId) {
        const detail = document.querySelector("#artist-detail-content");
        const detailEmpty = document.querySelector("#artist-detail-empty");
        if (detail) detail.hidden = false;
        if (detailEmpty) detailEmpty.hidden = true;
        const initialList = document.querySelector("#artist-track-list");
        if (initialList) loading(initialList, "Loading this artist’s saved tracks…");
        const response = await getJSON(endpoint(`/api/artists/${encodeURIComponent(artistId)}`, { limit, offset: state.offsets.artistTracks, sort: state.sorts.artistTracks }, { includeArtist: false }));
        if (requestId !== state.request) return;
        if (state.artistName !== response.artist.name) {
          state.artistName = response.artist.name;
          renderChips();
        }
        const heading = document.querySelector("#artist-detail-heading");
        const avatar = document.querySelector("#artist-detail-avatar");
        if (avatar) {
          avatar.replaceChildren();
          const imageUrl = httpsUrl(response.artist.image_url);
          if (imageUrl) {
            const image = make("img", "", "");
            image.src = imageUrl;
            image.alt = response.artist.name;
            image.addEventListener("error", () => {
              if (avatar.firstChild === image) { avatar.replaceChildren(); avatar.textContent = "♫"; }
            }, { once: true });
            avatar.append(image);
          } else avatar.textContent = "♫";
        }
        const profileLink = document.querySelector("#artist-detail-link");
        const profileHref = spotifyHref(response.artist.spotify_url);
        if (profileLink) {
          profileLink.hidden = !profileHref;
          if (profileHref) {
            profileLink.href = profileHref;
            profileLink.target = "_blank";
            profileLink.rel = "noopener noreferrer";
          } else profileLink.removeAttribute("href");
        }
        if (heading) heading.textContent = response.artist.name;
        const name = document.querySelector("#artist-detail-name");
        if (name) name.textContent = response.artist.name;
        const rank = document.querySelector("#artist-detail-rank");
        if (rank) rank.textContent = response.rank ? `Rank #${response.rank} · ` : "Unranked · ";
        const count = document.querySelector("#artist-detail-saved-count");
        if (count) count.textContent = `${number(response.saved_tracks)} saved tracks`;
        const totalDuration = document.querySelector("#artist-detail-duration");
        if (totalDuration) totalDuration.textContent = duration(response.total_duration_ms);
        let trackSort = document.querySelector("#artist-track-sort");
        if (!trackSort && detail) {
          const label = make("label", "filter-field sort-field", "Sort artist tracks");
          trackSort = make("select");
          trackSort.id = "artist-track-sort";
          [["added_at_desc", "Recently saved"], ["added_at_asc", "Oldest saved"], ["name_asc", "Name A–Z"], ["duration_desc", "Longest first"]].forEach(([value, title]) => {
            const option = make("option", "", title); option.value = value; trackSort.append(option);
          });
          label.append(trackSort);
          const tracksHeading = Array.from(detail.querySelectorAll("h4")).find((heading) => heading.textContent === "Tracks");
          detail.insertBefore(label, tracksHeading || detail.firstChild);
          trackSort.addEventListener("change", () => { state.sorts.artistTracks = trackSort.value; state.offsets.artistTracks = 0; loadScreen(); });
        }
        if (trackSort) trackSort.value = state.sorts.artistTracks;
        data = response.tracks;
        const root = document.querySelector("#artist-track-list");
        if (root) {
          root.replaceChildren();
          if (!data.items.length) empty(root, "No saved tracks by this artist match these filters.");
          else {
            const list = make("ol", "artist-track-list");
            data.items.forEach((track) => {
              const item = make("li", "artist-track");
              const credits = track.artists.map((artist) => artist.name).join(", ");
              item.append(make("strong", "", track.name), make("span", "", `${track.album?.name || "Unknown album"} · ${credits} · ${safeDate(track.added_at)} · ${duration(track.duration_ms)}`));
              list.append(item);
            });
            root.append(list);
            if (data.total > data.limit) {
              const pages = make("div", "pagination");
              const previous = make("button", "button secondary", "Previous");
              previous.type = "button"; previous.disabled = data.offset <= 0;
              previous.addEventListener("click", () => { state.offsets.artistTracks = Math.max(0, data.offset - data.limit); loadScreen(); });
              const label = make("span", "", `Page ${Math.floor(data.offset / data.limit) + 1} of ${Math.ceil(data.total / data.limit)}`);
              const next = make("button", "button secondary", "Next");
              next.type = "button"; next.disabled = data.offset + data.limit >= data.total;
              next.addEventListener("click", () => { state.offsets.artistTracks = data.offset + data.limit; loadScreen(); });
              pages.append(previous, label, next); root.append(pages);
            }
          }
        }
      } else data = await getJSON(endpoint("/api/tracks", { limit, offset: state.offsets.tracks, sort: state.sorts.tracks }));
      if (requestId !== state.request) return;
      if (!artistId) {
        const body = document.querySelector("#tracks-table-body");
        const count = document.querySelector("#tracks-result-count");
        if (count) count.textContent = `${number(data.total)} tracks`;
        if (body) {
          body.replaceChildren();
          if (!data.items.length) {
            const row = make("tr"); const cell = make("td", "table-state", "No saved tracks match these filters."); cell.colSpan = 5; row.append(cell); body.append(row);
          } else data.items.forEach((track, index) => {
            const row = make("tr");
            const titleCell = make("td");
            const href = spotifyHref(track.spotify_url);
            const title = href ? make("a", "table-link", track.name) : make("span", "", track.name);
            if (href) { title.href = href; title.target = "_blank"; title.rel = "noopener noreferrer"; }
            titleCell.append(title);
            row.append(make("td", "", data.offset + index + 1), titleCell);
            row.append(make("td", "", `${track.album?.name || "—"} · ${track.artists.map((artist) => artist.name).join(", ")}`));
            row.append(make("td", "", safeDate(track.added_at)), make("td", "", duration(track.duration_ms)));
            body.append(row);
          });
        }
        const previous = document.getElementById("tracks-previous");
        const next = document.getElementById("tracks-next");
        const pageLabel = document.getElementById("tracks-page-label");
        if (pageLabel) pageLabel.textContent = data.total ? `Page ${Math.floor(data.offset / data.limit) + 1} of ${Math.max(1, Math.ceil(data.total / data.limit))}` : "Page 0";
        if (previous) previous.disabled = data.offset <= 0;
        if (next) next.disabled = data.offset + data.limit >= data.total;
      }
    } catch (error) {
      if (requestId !== state.request) return;
      if (artistId) {
        const detail = document.querySelector("#artist-detail-content");
        if (detail) detail.hidden = false;
        const emptyMessage = document.querySelector("#artist-detail-empty");
        if (emptyMessage) emptyMessage.hidden = true;
        showError(document.querySelector("#artist-track-list"), error);
      } else {
        const body = document.querySelector("#tracks-table-body");
        if (body) {
          const row = make("tr");
          const cell = make("td", "table-state", error.message); cell.colSpan = 5;
          row.append(cell); body.replaceChildren(row);
        }
      }
    }
  }
  async function renderTimelineChart(id, requestId, interval = state.interval) {
    const node = chartNode(id);
    if (!node) return;
    clearChart(node, "Loading timeline…");
    try {
      const data = await getJSON(endpoint("/api/timeline", { interval }));
      if (requestId !== state.request) return;
      const buckets = data.buckets || [];
      if (!buckets.length) { clearChart(node, "No saved tracks match these filters."); return; }
      plot(id, [{ type: "bar", x: buckets.map((bucket) => bucket.start), y: buckets.map((bucket) => bucket.tracks), customdata: buckets.map((bucket) => [bucket.start, bucket.end]), marker: { color: "#8b5cf6" }, hovertemplate: "%{x}: %{y} saved tracks<extra></extra>" }], { xaxis: { title: interval === "month" ? "Month" : "Year", type: "date" }, yaxis: { title: "Saved tracks", rangemode: "tozero" } });
      node.removeAllListeners?.("plotly_click");
      node.on?.("plotly_click", (event) => {
        const dates = event?.points?.[0]?.customdata;
        if (!dates) return;
        if (filters.added_from) filters.added_from.value = filters.added_from.value && filters.added_from.value > dates[0] ? filters.added_from.value : dates[0];
        if (filters.added_to) filters.added_to.value = filters.added_to.value && filters.added_to.value < dates[1] ? filters.added_to.value : dates[1];
        resetPages();
        navigate("tracks");
      });
    } catch (error) { if (requestId === state.request) clearChart(node, `Timeline unavailable: ${error.message}`); }
  }
  async function renderTimeline(requestId) {
    const intervalControl = document.querySelector('input[name="timeline-interval"]:checked');
    if (intervalControl) state.interval = intervalControl.value;
    await renderTimelineChart("timeline-chart", requestId);
    if (requestId !== state.request) return;
    const from = document.querySelector("#timeline-from");
    const to = document.querySelector("#timeline-to");
    if (from && filters.added_from) from.value = filters.added_from.value;
    if (to && filters.added_to) to.value = filters.added_to.value;
  }
  async function renderDiscovery(requestId) {
    const root = document.querySelector("#discovery-insights") || contentRoot(screens.discovery, "discovery");
    loading(root);
    try {
      const data = await getJSON(endpoint("/api/insights"));
      if (requestId !== state.request) return;
      root.replaceChildren();
      if (!data.items?.length) { empty(root, "No insights are available for these filters."); return; }
      data.items.forEach((item) => {
        const card = make("article", "panel insight-card");
        card.append(make("span", "insight-icon", "✳"), make("h3", "", item.title), make("p", "", item.description));
        if (item.artist_id) {
          const action = make("button", "text-button", "View artist");
          action.type = "button"; action.addEventListener("click", () => openArtist(item.artist_id));
          card.append(action);
        }
        root.append(card);
      });
    } catch (error) { if (requestId === state.request) showError(root, error); }
  }
  async function renderSyncStatus() {
    const badge = document.querySelector("#sync-status-badge");
    const message = document.querySelector("#sync-message");
    const button = document.querySelector("#sync-button");
    try {
      const status = await getJSON("/api/sync/status");
      const previousLibrary = state.localLibrary;
      if (status.status !== "running") {
        state.localLibrary = { accountId: status.account_id, syncedAt: status.last_successful_sync };
      }
      if (status.status === "succeeded" && previousLibrary && previousLibrary.syncedAt !== status.last_successful_sync) {
        if (previousLibrary.accountId !== status.account_id) clearArtist();
        resetPages();
        if (state.screen !== "settings") loadScreen();
      }
      const labels = { idle: "Not synced", running: "Syncing", succeeded: "Synced", failed: "Sync failed" };
      if (badge) { badge.textContent = labels[status.status] || status.status; badge.className = `status-badge status-${status.status}`; }
      const last = document.querySelector("#last-sync-time");
      if (last) last.textContent = status.last_successful_sync ? safeDateTime(status.last_successful_sync) : "Not available";
      [["sync-saved-tracks", status.saved_tracks], ["sync-artists", status.artists], ["sync-albums", status.albums]].forEach(([id, value]) => {
        const node = document.getElementById(id); if (node) node.textContent = number(value);
      });
      if (message) { message.textContent = status.error || (status.status === "running" ? `Processed ${number(status.processed_tracks)} tracks so far.` : `Library sync status: ${status.status}.`); message.removeAttribute("role"); }
      const sidebarStatus = document.querySelector("#sidebar-connection-status");
      if (sidebarStatus) sidebarStatus.textContent = status.account_id ? "Local library available" : "No local library";
      if (button) { button.disabled = status.status === "running"; button.textContent = status.status === "running" ? "Sync in progress" : "Sync library"; }
      const region = document.querySelector("#sync-progress-region");
      if (region) region.hidden = status.status !== "running";
      const progressMessage = document.querySelector("#sync-progress-message");
      if (progressMessage && status.status === "running") progressMessage.textContent = `Processed ${number(status.processed_tracks)} tracks.`;
      const progress = document.querySelector("#sync-progress");
      if (progress) { progress.removeAttribute("value"); progress.setAttribute("aria-label", `${number(status.processed_tracks)} tracks processed`); }
      if (state.syncPoll) clearTimeout(state.syncPoll);
      state.syncPoll = status.status === "running" ? setTimeout(renderSyncStatus, 1800) : null;
      const connectionBadge = document.querySelector("#connection-status-badge");
      const connectionMessage = document.querySelector("#connection-status-message");
      if (connectionBadge) {
        connectionBadge.textContent = status.account_id ? "Library available" : "No saved library";
        connectionBadge.className = `status-badge ${status.account_id ? "status-connected" : "status-idle"}`;
      }
      if (connectionMessage) connectionMessage.textContent = status.account_id ? "A local library is available. Reconnect to Spotify whenever you want to sync again." : "Connect Spotify to sync a library. Local analytics remain available.";
    } catch (error) {
      if (badge) { badge.textContent = "Unavailable"; badge.className = "status-badge status-error"; }
      if (message) { message.textContent = error.message; message.setAttribute("role", "alert"); }
      if (button) button.disabled = false;
      const sidebarStatus = document.querySelector("#sidebar-connection-status");
      if (sidebarStatus) sidebarStatus.textContent = "Unavailable";
      const connectionBadge = document.querySelector("#connection-status-badge");
      const connectionMessage = document.querySelector("#connection-status-message");
      if (connectionBadge) { connectionBadge.textContent = "Unavailable"; connectionBadge.className = "status-badge status-error"; }
      if (connectionMessage) connectionMessage.textContent = error.message;
    }
  }
  function bindSync() {
    const button = document.querySelector("#sync-button");
    if (!button) return;
    button.addEventListener("click", async () => {
      const message = document.querySelector("#sync-message");
      const region = document.querySelector("#sync-progress-region");
      if (message) { message.textContent = "Starting library sync…"; message.setAttribute("role", "status"); }
      if (region) region.hidden = false;
      button.disabled = true; button.setAttribute("aria-busy", "true");
      try {
        await getJSON("/api/sync", { method: "POST" });
        await renderSyncStatus();
      } catch (error) {
        if (message) { message.textContent = error.message; message.setAttribute("role", "alert"); }
        if (region) region.hidden = true;
        button.disabled = false;
      } finally { button.removeAttribute("aria-busy"); }
    });
  }
  function openArtist(id, name = "") {
    state.artistId = id;
    state.artistName = name;
    state.offsets.tracks = 0;
    state.offsets.artistTracks = 0;
    navigate("artists");
  }
  function loadScreen() {
    const requestId = ++state.request;
    renderChips();
    const title = ({ overview: "Overview", artists: "Artists", tracks: "Tracks", timeline: "Timeline", discovery: "Discovery", settings: "Settings" })[state.screen] || "Library";
    const titleNode = document.querySelector("#page-title");
    if (titleNode) titleNode.textContent = title;
    const status = document.querySelector("#dashboard-status");
    if (status) status.textContent = `${title} selected.`;
    if (state.screen === "overview") renderOverview(requestId);
    else if (state.screen === "artists") {
      renderArtists(requestId);
      if (state.artistId) renderTracks(requestId, state.artistId);
    }
    else if (state.screen === "tracks") renderTracks(requestId);
    else if (state.screen === "timeline") renderTimeline(requestId);
    else if (state.screen === "discovery") renderDiscovery(requestId);
    else if (state.screen === "settings") renderSyncStatus();
  }
  function initDashboard() {
    if (!screens.overview) return false;
    document.querySelectorAll("[data-screen]").forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault();
      if (link.dataset.screen === "artists" && state.artistId) clearArtist();
      navigate(link.dataset.screen);
    }));
    filtersForm?.addEventListener("submit", (event) => { event.preventDefault(); applyFilters(); });
    Object.values(filters).forEach((input) => input?.addEventListener("change", applyFilters));
    document.querySelector("#track-search")?.addEventListener("input", (event) => {
      if (filters.q) {
        filters.q.value = event.target.value;
        filters.q.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    filters.q?.addEventListener("input", () => {
      const trackSearch = document.querySelector("#track-search");
      const artistSearch = document.querySelector("#artist-search");
      if (trackSearch && trackSearch.value !== filters.q.value) trackSearch.value = filters.q.value;
      if (artistSearch && artistSearch.value !== filters.q.value) artistSearch.value = filters.q.value;
      clearTimeout(filters.q._debounce);
      filters.q._debounce = setTimeout(applyFilters, 250);
    });
    document.querySelector("#clear-filters")?.addEventListener("click", clearFilters);
    document.querySelector("#artist-sort")?.addEventListener("change", (event) => { state.sorts.artists = event.target.value; state.offsets.artists = 0; loadScreen(); });
    document.querySelector("#track-sort")?.addEventListener("change", (event) => { state.sorts.tracks = event.target.value; state.offsets.tracks = 0; loadScreen(); });
    document.querySelector("#artist-search")?.addEventListener("input", (event) => {
      if (!filters.q) return;
      clearArtist();
      filters.q.value = event.target.value;
      filters.q.dispatchEvent(new Event("input", { bubbles: true }));
    });
    document.querySelectorAll("#tracks-previous, #tracks-next").forEach((button) => button.addEventListener("click", () => {
      state.offsets.tracks = Math.max(0, state.offsets.tracks + (button.id.endsWith("next") ? 25 : -25)); loadScreen();
    }));
    document.querySelectorAll("#artists-previous, #artists-next").forEach((button) => button.addEventListener("click", () => {
      state.offsets.artists = Math.max(0, state.offsets.artists + (button.id.endsWith("next") ? 25 : -25)); loadScreen();
    }));
    document.querySelectorAll('input[name="timeline-interval"]').forEach((input) => input.addEventListener("change", () => { state.interval = input.value; loadScreen(); }));
    document.querySelector("#timeline-apply-range")?.addEventListener("click", () => {
      if (filters.added_from) filters.added_from.value = document.querySelector("#timeline-from")?.value || "";
      if (filters.added_to) filters.added_to.value = document.querySelector("#timeline-to")?.value || "";
      applyFilters();
    });
    document.querySelector("#timeline-clear-range")?.addEventListener("click", () => {
      if (filters.added_from) filters.added_from.value = "";
      if (filters.added_to) filters.added_to.value = "";
      applyFilters();
    });
    document.querySelectorAll('[name="dimension"]').forEach((select) => select.addEventListener("change", () => { state.dimension = select.value; renderDistributionChart(); }));
    bindSync();
    const hashScreen = location.hash.startsWith("#screen-") ? location.hash.slice(8) : location.hash.slice(1);
    if (screens[hashScreen]) state.screen = hashScreen;
    renderNav(); loadScreen(); renderSyncStatus();
    return true;
  }
  initDashboard();
})();
