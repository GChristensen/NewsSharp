// landing/main.js: page logic for landing/index.html, mostly the interactive NewsSharp preview.
(function () {
  "use strict";

  // ---- iframe-aware link handling ----
  // When embedded in an iframe, links open in a new tab; standalone, they navigate in the same tab.
  function is_embedded() {
    try {
      return window.self !== window.top;
    } catch (e) {
      // cross-origin parent access throws, which itself implies embedding
      return true;
    }
  }

  if (is_embedded()) {
    var links = document.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i++) {
      links[i].target = "_blank";
      links[i].rel = "noopener noreferrer";
    }
  }

  // ---- blurred backdrop ----
  // Fills the wall with forty story-card shapes in eight columns: a picture in one of the eight --wall-N
  // colors and two text lines. Heights and colors repeat in a fixed pattern; styles.css blurs them.
  var wall = document.querySelector("[data-wall]");
  if (wall) {
    var tile_heights = [210, 260, 180, 240, 300, 200, 250, 220];
    for (var t = 0; t < 40; t++) {
      var tile = document.createElement("i");
      tile.style.setProperty("--h", tile_heights[(t * 3) % 8] + "px");
      tile.style.setProperty("--c", "var(--wall-" + ((t * 5 + Math.floor(t / 8)) % 8 + 1) + ")");
      tile.appendChild(document.createElement("b"));
      tile.appendChild(document.createElement("b"));
      tile.appendChild(document.createElement("b"));
      wall.appendChild(tile);
    }
  }

  // ---- interactive preview ----
  var app = document.getElementById("app");
  if (!app) return;

  function $(selector, root) { return (root || app).querySelector(selector); }
  function $$(selector, root) { return Array.prototype.slice.call((root || app).querySelectorAll(selector)); }

  var list = $("[data-list]");
  var pane = $(".m-pane");
  var detail = $("[data-detail]");
  var footer = $("[data-footer]");
  var hint = $("[data-hint]");
  var stories = $$("[data-story]");
  var feed_rows = $$("[data-feed]");
  var current_feed = $("[data-current]").getAttribute("data-feed");

  // Unread counts of the feeds that are not open; the open feed's count comes from its stories.
  var counts = {};
  feed_rows.forEach(function (row) {
    var badge = $(".m-count", row);
    counts[row.getAttribute("data-feed")] = badge ? parseInt(badge.textContent, 10) : 0;
  });

  var state = { layout: "split", filter: "all", sidebar: "all", query: "", selected: null };

  // ---------- counts and the feed tree ----------
  function unread_in_current() {
    return stories.filter(function (s) { return !s.classList.contains("read"); }).length;
  }
  function saved_in_current() {
    return stories.filter(function (s) { return s.classList.contains("saved"); }).length;
  }

  function set_count(row, value, saved) {
    var badge = $(".m-count", row);
    if (value <= 0) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement(saved ? "span" : "button");
      if (!saved) { badge.type = "button"; badge.title = "Mark as read"; }
      badge.className = "m-count";
      row.appendChild(badge);
    }
    badge.classList.toggle("saved", !!saved);
    badge.classList.toggle("static", !!saved);
    badge.textContent = value;
  }

  function render_tree() {
    counts[current_feed] = unread_in_current();
    var saved_mode = state.sidebar === "saved";
    var total = 0;
    var needle = (($("[data-filter-input]") || {}).value || "").trim().toLowerCase();

    feed_rows.forEach(function (row) {
      var id = row.getAttribute("data-feed");
      var is_current = id === current_feed;
      var value = saved_mode ? (is_current ? saved_in_current() : 0) : counts[id];
      total += value;
      set_count(row, value, saved_mode);
      row.classList.toggle("empty", value === 0);

      var title = $(".m-title", row).textContent.toLowerCase();
      var visible = (state.sidebar === "all" || value > 0 || is_current) && (!needle || title.indexOf(needle) >= 0);
      row.hidden = !visible || row.hasAttribute("data-collapsed");
    });

    // A folder stays visible while any of its feeds would be, collapsed or not.
    $$("[data-folder]").forEach(function (folder) {
      var any = false;
      for (var next = folder.nextElementSibling; next && !next.hasAttribute("data-folder"); next = next.nextElementSibling) {
        if (!next.hidden || next.hasAttribute("data-collapsed")) any = true;
      }
      folder.hidden = !any;
    });

    $("[data-all-label]").textContent = saved_mode ? "All Saved Stories" : "All Site Stories";
    var all_count = $("[data-all-count]");
    all_count.textContent = total;
    all_count.hidden = total === 0;
    all_count.classList.toggle("saved", saved_mode);

    var scope_count = $("[data-scope-count]");
    scope_count.textContent = counts[current_feed];
    scope_count.hidden = counts[current_feed] === 0;
  }

  // ---------- stories ----------
  function is_visible(story) {
    if (story === state.selected) return true;
    if (state.filter === "unread" && story.classList.contains("read")) return false;
    if (state.query && $(".m-story-title", story).textContent.toLowerCase().indexOf(state.query) < 0) return false;
    return true;
  }

  function render_list() {
    var shown = 0;
    stories.forEach(function (story) {
      story.hidden = !is_visible(story);
      if (!story.hidden) shown++;
    });
    if (shown > 0) footer.textContent = "End of stories";
    else if (state.query) footer.textContent = "No stories match the search.";
    else footer.textContent = "No unread stories.";
  }

  function set_read(story, read) {
    story.classList.toggle("read", read);
    if (story === state.selected) render_detail();
    render_tree();
  }

  function render_detail() {
    var story = state.selected;
    detail.hidden = !story;
    hint.hidden = !!story;
    if (!story) return;
    $("[data-detail-title]", detail).textContent = $(".m-story-title", story).textContent;
    $("[data-detail-author]", detail).textContent = story.getAttribute("data-author");
    $("[data-detail-date]", detail).textContent = story.getAttribute("data-date");

    var tags = $("[data-detail-tags]", detail);
    tags.textContent = "";
    story.getAttribute("data-tags").split(",").forEach(function (tag) {
      var chip = document.createElement("span");
      chip.className = "m-tag";
      chip.textContent = tag.trim();
      tags.appendChild(chip);
    });

    var body = $("[data-detail-body]", detail);
    body.textContent = "";
    var image = $(".m-thumb img", story).cloneNode(true);
    image.removeAttribute("width");
    image.removeAttribute("height");
    body.appendChild(image);
    body.appendChild($("template", story).content.cloneNode(true));

    var saved = story.classList.contains("saved");
    var save = $("[data-action=save]", detail);
    save.classList.toggle("active", saved);
    $("span", save).textContent = saved ? "Saved for Later" : "Save for later";

    var read = story.classList.contains("read");
    var toggle = $("[data-action=toggle-read]", detail);
    $("use", toggle).setAttribute("href", read ? "#i-circle" : "#i-check");
    $("span", toggle).textContent = read ? "Mark unread" : "Mark read";
    detail.classList.toggle("read", read);
  }

  // Split and Full show the open story in the story pane; List, Magazine and Grid open it below its row.
  function place_detail() {
    var inline = state.layout === "list" || state.layout === "magazine" || state.layout === "grid";
    if (inline && state.selected) state.selected.after(detail);
    else if (detail.parentNode !== pane) pane.appendChild(detail);
    detail.classList.toggle("inline", inline);
  }

  function select(story, mark) {
    if (state.selected) state.selected.classList.remove("selected");
    state.selected = story;
    story.classList.add("selected");
    if (mark !== false) story.classList.add("read");
    place_detail();
    render_detail();
    render_tree();
    render_list();
    // Scroll inside the preview only: scrollIntoView would also move the page.
    if (detail.classList.contains("inline")) {
      list.scrollTop = story.offsetTop;
    } else {
      if (story.offsetTop < list.scrollTop) list.scrollTop = story.offsetTop;
      var bottom = story.offsetTop + story.offsetHeight - list.clientHeight;
      if (bottom > list.scrollTop) list.scrollTop = bottom;
      pane.scrollTop = 0;
    }
  }

  function close_story() {
    state.selected.classList.remove("selected");
    state.selected = null;
    place_detail();
    render_detail();
    render_list();
  }

  function step(direction) {
    var visible = stories.filter(function (s) { return !s.hidden; });
    var index = visible.indexOf(state.selected) + direction;
    if (index >= 0 && index < visible.length) select(visible[index]);
  }

  // ---------- segmented controls ----------
  function set_segment(group, value) {
    $$("[data-group=" + group + "] .m-btn").forEach(function (button) {
      var on = button.getAttribute("data-value") === value;
      button.classList.toggle("active", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  var on_segment = {
    layout: function (value) {
      state.layout = value;
      app.setAttribute("data-layout", value);
      place_detail();
    },
    filter: function (value) { state.filter = value; render_list(); },
    order: function (value) {
      var newest_first = value === "newest";
      stories.sort(function (a, b) {
        var ia = parseInt(a.getAttribute("data-index"), 10);
        var ib = parseInt(b.getAttribute("data-index"), 10);
        return newest_first ? ia - ib : ib - ia;
      });
      stories.forEach(function (story) { list.insertBefore(story, footer); });
      place_detail();
    },
    sidebar: function (value) { state.sidebar = value; render_tree(); },
    view: function () {},
  };

  stories.forEach(function (story, index) { story.setAttribute("data-index", index); });

  // ---------- events ----------
  app.addEventListener("click", function (event) {
    var target = event.target;

    var segment = target.closest("[data-group] .m-btn");
    if (segment) {
      var group = segment.closest("[data-group]").getAttribute("data-group");
      var value = segment.getAttribute("data-value");
      set_segment(group, value);
      on_segment[group](value);
      return;
    }

    var badge = target.closest("button.m-count");
    if (badge) {
      var id = badge.closest("[data-feed]").getAttribute("data-feed");
      if (id === current_feed) stories.forEach(function (s) { s.classList.add("read"); });
      counts[id] = 0;
      render_detail();
      render_tree();
      render_list();
      return;
    }

    var toggle = target.closest("[data-toggle]");
    if (toggle) {
      var folder = toggle.closest("[data-folder]");
      var open = !toggle.classList.contains("open");
      toggle.classList.toggle("open", open);
      for (var row = folder.nextElementSibling; row && !row.hasAttribute("data-folder"); row = row.nextElementSibling) {
        if (open) row.removeAttribute("data-collapsed");
        else row.setAttribute("data-collapsed", "");
      }
      render_tree();
      return;
    }

    var story = target.closest("[data-story]");
    if (story && !target.closest("[data-detail]")) {
      // As in the app, a second click on a story opened below its row folds it again.
      if (story === state.selected && detail.classList.contains("inline")) close_story();
      else select(story);
      return;
    }

    var action = target.closest("[data-action]");
    if (!action) return;
    switch (action.getAttribute("data-action")) {
      case "prev": step(-1); break;
      case "next": step(1); break;
      case "mark-all":
        stories.forEach(function (s) { s.classList.add("read"); });
        render_detail();
        render_tree();
        render_list();
        break;
      case "save":
        state.selected.classList.toggle("saved");
        render_detail();
        render_tree();
        break;
      case "toggle-read":
        set_read(state.selected, !state.selected.classList.contains("read"));
        break;
      case "filter": {
        var bar = $("[data-filter-bar]");
        bar.hidden = !bar.hidden;
        action.classList.toggle("active", !bar.hidden);
        if (bar.hidden) $("[data-filter-input]").value = "";
        else $("[data-filter-input]").focus();
        render_tree();
        break;
      }
      case "collapse":
        app.classList.add("collapsed");
        $("[data-action=expand]").hidden = false;
        break;
      case "expand":
        app.classList.remove("collapsed");
        action.hidden = true;
        break;
    }
  });

  $("[data-filter-input]").addEventListener("input", render_tree);
  $("[data-search-input]").addEventListener("input", function (event) {
    state.query = event.target.value.trim().toLowerCase();
    render_list();
  });

  // The preview starts with one story open, left unread like the stories after it.
  select($("[data-initial]"), false);
})();
