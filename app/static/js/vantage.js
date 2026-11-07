/* Vantage frontend: small, framework-free. Everything is (re)initialised per
   swapped widget via init(root), so HTMX fragments behave like the first paint. */
(function () {
  "use strict";

  var baseUrl = document.body.getAttribute("data-base-url") || "";

  // ------------------------------------------------------------ relative time
  function fmtDelta(seconds) {
    var s = Math.abs(seconds);
    if (s < 60) return "now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    if (s < 86400 * 30) return Math.floor(s / 86400) + "d";
    if (s < 86400 * 365) return Math.floor(s / (86400 * 30)) + "mo";
    return Math.floor(s / (86400 * 365)) + "y";
  }
  function updateTimes(root) {
    var now = Date.now() / 1000;
    root.querySelectorAll("[data-dynamic-relative-time]").forEach(function (el) {
      var ts = +el.getAttribute("data-dynamic-relative-time");
      var d = ts - now;
      var text = fmtDelta(d);
      el.textContent = d > 60 ? "in " + text : text;
    });
    root.querySelectorAll("[data-dynamic-countdown]").forEach(function (el) {
      var d = +el.getAttribute("data-dynamic-countdown") - now;
      if (d <= 0) { el.textContent = "now"; return; }
      var days = Math.floor(d / 86400), hours = Math.floor((d % 86400) / 3600), mins = Math.floor((d % 3600) / 60);
      el.textContent = days > 0 ? days + "d " + hours + "h" : hours > 0 ? hours + "h " + mins + "m" : mins + "m";
    });
  }

  // ------------------------------------------------------------ collapsible lists
  function initCollapsible(root) {
    root.querySelectorAll(".collapsible[data-collapse-after]:not([data-ready])").forEach(function (list) {
      list.setAttribute("data-ready", "");
      var after = +list.getAttribute("data-collapse-after");
      var items = Array.prototype.filter.call(list.children, function (c) { return c.tagName !== "BUTTON"; });
      if (after < 0 || items.length <= after) return;
      items.slice(after).forEach(function (el) { el.classList.add("collapsible-item-hidden"); });
      list.classList.add("collapsed");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "expand-toggle";
      var hidden = items.length - after;
      var label = function () { return list.classList.contains("collapsed") ? "Show " + hidden + " more" : "Show less"; };
      btn.textContent = label();
      btn.addEventListener("click", function () { list.classList.toggle("collapsed"); btn.textContent = label(); });
      list.insertAdjacentElement("afterend", btn);
    });
  }

  // ------------------------------------------------------------ clock
  var clocks = [];
  function initClocks(root) {
    root.querySelectorAll("[data-clock]:not([data-ready])").forEach(function (el) {
      el.setAttribute("data-ready", "");
      clocks.push(el);
    });
    tickClocks();
  }
  function tzParts(date, tz, hour12) {
    try {
      return {
        time: new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit", hour12: hour12, timeZone: tz || undefined }).format(date),
        date: new Intl.DateTimeFormat(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: tz || undefined }).format(date),
        short: new Intl.DateTimeFormat(undefined, { weekday: "short", timeZone: tz || undefined }).format(date),
      };
    } catch (e) { return { time: "bad tz", date: tz, short: "" }; }
  }
  function offsetLabel(date, tz) {
    try {
      var local = new Date(date.toLocaleString("en-US"));
      var there = new Date(date.toLocaleString("en-US", { timeZone: tz }));
      var h = Math.round((there - local) / 36e5);
      return h === 0 ? "" : (h > 0 ? "+" : "") + h + "h";
    } catch (e) { return ""; }
  }
  function tickClocks() {
    var now = new Date();
    clocks = clocks.filter(function (el) { return el.isConnected; });
    clocks.forEach(function (el) {
      var hour12 = el.getAttribute("data-hour-format") !== "24h";
      var main = tzParts(now, el.getAttribute("data-main-tz"), hour12);
      var t = main.time.split(" ");
      var mainEl = el.querySelector(".clock-main");
      if (mainEl) mainEl.innerHTML = t[0] + (t[1] ? "<small>" + t[1] + "</small>" : "");
      var dateEl = el.querySelector(".clock-date");
      if (dateEl) dateEl.textContent = main.date;
      el.querySelectorAll("[data-tz]").forEach(function (row) {
        var tz = row.getAttribute("data-tz");
        var p = tzParts(now, tz, hour12);
        row.querySelector(".tz-time").textContent = p.time;
        var off = row.querySelector(".tz-offset");
        if (off) off.textContent = [p.short, offsetLabel(now, tz)].filter(Boolean).join(" ");
      });
    });
  }

  // ------------------------------------------------------------ calendar
  function initCalendars(root) {
    root.querySelectorAll("[data-calendar]:not([data-ready])").forEach(function (el) {
      el.setAttribute("data-ready", "");
      var firstDow = +el.getAttribute("data-first-day") || 0; // 0 = Sunday
      var view = new Date(); view.setDate(1);
      var render = function () {
        var today = new Date();
        var grid = el.querySelector(".cal-grid");
        var title = el.querySelector(".cal-title");
        title.innerHTML = view.toLocaleString(undefined, { month: "long" }) + "<small>" + view.getFullYear() + "</small>";
        var html = "";
        for (var i = 0; i < 7; i++) {
          var d = new Date(2023, 0, 1 + ((firstDow + i) % 7)); // 2023-01-01 is a Sunday
          html += '<div class="dow">' + d.toLocaleString(undefined, { weekday: "short" }).slice(0, 2) + "</div>";
        }
        var start = new Date(view);
        start.setDate(1 - ((view.getDay() - firstDow + 7) % 7));
        for (var n = 0; n < 42; n++) {
          var day = new Date(start); day.setDate(start.getDate() + n);
          if (n === 35 && day.getMonth() !== view.getMonth()) break;
          var cls = "day";
          if (day.getMonth() !== view.getMonth()) cls += " other";
          if (day.toDateString() === today.toDateString()) cls += " today";
          html += '<div class="' + cls + '">' + day.getDate() + "</div>";
        }
        grid.innerHTML = html;
      };
      el.querySelector("[data-cal-prev]").addEventListener("click", function () { view.setMonth(view.getMonth() - 1); render(); });
      el.querySelector("[data-cal-next]").addEventListener("click", function () { view.setMonth(view.getMonth() + 1); render(); });
      el.querySelector("[data-cal-today]").addEventListener("click", function () { view = new Date(); view.setDate(1); render(); });
      render();
    });
  }

  // ------------------------------------------------------------ search
  function initSearch(root) {
    root.querySelectorAll("[data-search]:not([data-ready])").forEach(function (form) {
      form.setAttribute("data-ready", "");
      var input = form.querySelector("input");
      var badge = form.querySelector(".search-bang");
      var engine = form.getAttribute("data-engine");
      var newTab = form.hasAttribute("data-new-tab");
      var bangs = JSON.parse(form.getAttribute("data-bangs") || "[]");
      var match = function (q) {
        for (var i = 0; i < bangs.length; i++) {
          var b = bangs[i];
          if (q === b.shortcut || q.indexOf(b.shortcut + " ") === 0) return { bang: b, query: q.slice(b.shortcut.length).trim() };
        }
        return { bang: null, query: q };
      };
      input.addEventListener("input", function () {
        var m = match(input.value.trim());
        badge.hidden = !m.bang;
        if (m.bang) badge.textContent = m.bang.title;
      });
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var q = input.value.trim();
        if (!q) return;
        var m = match(q);
        var tpl = m.bang ? m.bang.url : engine;
        var url = tpl.replace("{QUERY}", encodeURIComponent(m.query));
        // Ctrl+Enter flips the configured new-tab behaviour.
        var openNew = newTab !== !!window.__vantageShift;
        if (openNew) window.open(url, "_blank", "noopener,noreferrer"); else location.href = url;
        input.value = ""; badge.hidden = true;
      });
      input.addEventListener("keydown", function (e) {
        window.__vantageShift = e.key === "Enter" && e.ctrlKey;
        if (e.key === "Escape") input.blur();
      });
      if (form.hasAttribute("data-autofocus")) setTimeout(function () { input.focus(); }, 0);
    });
  }
  document.addEventListener("keydown", function (e) {
    if (e.key !== "/" && e.key !== "s") return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var input = document.querySelector("[data-search] input");
    if (input) { e.preventDefault(); input.focus(); }
  });

  // ------------------------------------------------------------ groups (tabs)
  function initGroups(root) {
    root.querySelectorAll("[data-group]:not([data-ready])").forEach(function (g) {
      g.setAttribute("data-ready", "");
      var tabs = g.querySelectorAll(":scope > .group-tabs > button");
      var panels = g.querySelectorAll(":scope > .widget-content > .group-panel, :scope > .group-panels > .group-panel");
      tabs.forEach(function (tab, i) {
        tab.addEventListener("click", function () {
          tabs.forEach(function (t, j) { t.setAttribute("aria-selected", i === j ? "true" : "false"); });
          panels.forEach(function (p, j) { p.hidden = i !== j; });
        });
      });
    });
  }

  // ------------------------------------------------------------ theme picker
  function initThemePicker() {
    var picker = document.querySelector(".theme-picker");
    if (!picker) return;
    var btn = picker.querySelector(".theme-picker-button");
    var menu = picker.querySelector(".theme-menu");
    var mark = function () {
      var current = document.documentElement.getAttribute("data-theme");
      menu.querySelectorAll("[data-theme-choice]").forEach(function (b) {
        b.setAttribute("aria-checked", b.getAttribute("data-theme-choice") === current ? "true" : "false");
      });
    };
    var close = function () { menu.hidden = true; btn.setAttribute("aria-expanded", "false"); };
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      btn.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
      mark();
    });
    menu.addEventListener("click", function (e) {
      var choice = e.target.closest("[data-theme-choice]");
      if (!choice) return;
      var name = choice.getAttribute("data-theme-choice");
      document.documentElement.setAttribute("data-theme", name);
      try { localStorage.setItem("vantage-theme", name); } catch (err) {}
      mark(); close();
    });
    document.addEventListener("click", function (e) { if (!picker.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  }

  // ------------------------------------------------------------ mobile columns
  function initMobileColumns() {
    var nav = document.querySelector(".mobile-columns");
    if (!nav) return;
    var page = document.querySelector(".page");
    var key = "vantage-col-" + (page && page.getAttribute("data-page"));
    var show = function (i) {
      document.querySelectorAll(".columns > .column").forEach(function (c) { c.classList.toggle("mobile-active", c.getAttribute("data-col") === String(i)); });
      nav.querySelectorAll("button").forEach(function (b) { b.classList.toggle("active", b.getAttribute("data-col") === String(i)); });
    };
    try { var saved = sessionStorage.getItem(key); if (saved !== null) show(saved); } catch (e) {}
    nav.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-col]");
      if (!b) return;
      var i = b.getAttribute("data-col");
      show(i);
      window.scrollTo(0, 0);
      try { sessionStorage.setItem(key, i); } catch (err) {}
    });
  }

  // ------------------------------------------------------------ config-change toast
  function watchConfig() {
    var version = document.body.getAttribute("data-version");
    var toast = document.getElementById("reload-toast");
    if (!version || !toast) return;
    setInterval(function () {
      if (document.hidden) return;
      fetch(baseUrl + "/healthz", { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (j) { if (String(j.version) !== version && j.config === "ok") toast.hidden = false; })
        .catch(function () {});
    }, 15000);
  }

  function init(root) {
    updateTimes(root);
    initCollapsible(root);
    initClocks(root);
    initCalendars(root);
    initSearch(root);
    initGroups(root);
  }

  document.addEventListener("DOMContentLoaded", function () {
    init(document);
    initThemePicker();
    initMobileColumns();
    watchConfig();
    setInterval(function () { updateTimes(document); }, 30000);
    setInterval(tickClocks, 1000);
  });
  document.addEventListener("htmx:afterSwap", function (e) { init(e.detail.elt.parentNode || document); });
  document.addEventListener("htmx:afterSettle", function (e) { init(e.detail.elt.parentNode || document); });
  document.addEventListener("htmx:responseError", function (e) {
    var el = e.detail.elt;
    if (!el || !el.classList || !el.classList.contains("is-loading")) return;
    var content = el.querySelector(".widget-content");
    if (content) content.innerHTML = '<div class="widget-error"><div class="error-head">Couldn\'t load</div><p class="error-msg">Vantage returned HTTP ' + e.detail.xhr.status + '</p></div>';
  });
  document.addEventListener("htmx:sendError", function (e) {
    var el = e.detail.elt;
    var content = el && el.querySelector && el.querySelector(".widget-content");
    if (content && el.classList.contains("is-loading")) content.innerHTML = '<div class="widget-error"><div class="error-head">Couldn\'t reach Vantage</div></div>';
  });
})();
