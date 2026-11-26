/* TSMP site: shared behaviour for the English and Russian pages.
   Everything here is an enhancement; the pages read fine without it.
   Words that differ by language come from data- attributes or <html lang>. */
(function () {
  "use strict";
  var d = document, w = window, root = d.documentElement;
  var raf = w.requestAnimationFrame || function (f) { return setTimeout(f, 16); };
  var mq = function (q) { return w.matchMedia ? w.matchMedia(q) : { matches: false }; };
  var reduce = mq("(prefers-reduced-motion: reduce)");
  var fine = mq("(hover: hover) and (pointer: fine)");
  var passive = { passive: true };
  var hasIO = "IntersectionObserver" in w;
  root.classList.add("js");

  function onChange(m, fn) {
    if (m.addEventListener) m.addEventListener("change", fn);
    else if (m.addListener) m.addListener(fn);
  }

  /* ---- header: transparent over the hero, a blurred bar after scrolling ---- */
  var header = d.querySelector("[data-header]");
  function headerState() {
    if (header) header.classList.toggle("is-scrolled", (w.scrollY || w.pageYOffset) > 12);
  }
  headerState();

  /* ---- hero parallax: scroll and (fine pointer only) mouse ---- */
  var hero = d.querySelector("[data-hero]");
  var layers = [], content = null, heroOn = true;
  var pointer = { tx: 0, ty: 0, x: 0, y: 0 };
  var queued = false;

  if (hero) {
    content = hero.querySelector("[data-hero-content]");
    Array.prototype.forEach.call(hero.querySelectorAll("[data-depth]"), function (el) {
      layers.push({ el: el, depth: parseFloat(el.getAttribute("data-depth")) || 0 });
    });
  }

  function paint() {
    queued = false;
    if (!hero) return;
    if (reduce.matches) { resetHero(); return; }
    var h = hero.offsetHeight || 1;
    var sy = Math.min(Math.max((w.scrollY || w.pageYOffset) - hero.offsetTop, 0), h);
    pointer.x += (pointer.tx - pointer.x) * 0.08;
    pointer.y += (pointer.ty - pointer.y) * 0.08;
    var moving = Math.abs(pointer.tx - pointer.x) > 0.001 || Math.abs(pointer.ty - pointer.y) > 0.001;
    for (var i = 0; i < layers.length; i++) {
      var l = layers[i];
      // far layers stay behind while the page moves; near ones move almost with it
      var y = sy * (1 - l.depth) - pointer.y * l.depth * 18;
      var x = -pointer.x * l.depth * 28;
      l.el.style.transform = "translate3d(" + x.toFixed(2) + "px," + y.toFixed(2) + "px,0)";
    }
    if (content) {
      content.style.transform = "translate3d(0," + (sy * 0.32).toFixed(2) + "px,0)";
      content.style.opacity = Math.max(0, 1 - sy / (h * 0.62)).toFixed(3);
    }
    if (moving && heroOn) queue();
  }
  function queue() { if (!queued) { queued = true; raf(paint); } }
  function resetHero() {
    layers.forEach(function (l) { l.el.style.transform = ""; });
    if (content) { content.style.transform = ""; content.style.opacity = ""; }
  }

  w.addEventListener("scroll", function () {
    headerState();
    if (heroOn) queue();
  }, passive);
  w.addEventListener("resize", function () { if (heroOn) queue(); fireflies.resize(); }, passive);

  if (hero) {
    hero.addEventListener("pointermove", function (e) {
      if (!fine.matches || reduce.matches || e.pointerType === "touch") return;
      pointer.tx = (e.clientX / w.innerWidth) * 2 - 1;
      pointer.ty = (e.clientY / w.innerHeight) * 2 - 1;
      queue();
    }, passive);
    hero.addEventListener("pointerleave", function () { pointer.tx = 0; pointer.ty = 0; queue(); }, passive);
    onChange(reduce, function () { queue(); fireflies.toggle(); });
    queue();
  }

  /* ---- fireflies: a few dozen soft lights on a small canvas ---- */
  var fireflies = (function () {
    var canvas = hero && hero.querySelector(".fireflies");
    var api = { resize: function () {}, toggle: function () {} };
    if (!canvas || !canvas.getContext) return api;
    // the lights are soft blobs: drawn at 1x and at most ~30 frames a second
    var ctx = canvas.getContext("2d"), dots = [], running = false, last = 0, W = 0, H = 0, ratio = 1, STEP = 1000 / 30 - 2;
    var sprite = d.createElement("canvas");
    sprite.width = sprite.height = 64;
    var s = sprite.getContext("2d"), g = s.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,246,214,1)");
    g.addColorStop(0.18, "rgba(255,214,120,.9)");
    g.addColorStop(0.45, "rgba(255,200,87,.25)");
    g.addColorStop(1, "rgba(255,200,87,0)");
    s.fillStyle = g; s.fillRect(0, 0, 64, 64);
    var mint = d.createElement("canvas");
    mint.width = mint.height = 64;
    s = mint.getContext("2d"); g = s.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(232,255,246,1)");
    g.addColorStop(0.18, "rgba(140,236,200,.85)");
    g.addColorStop(0.45, "rgba(99,217,176,.22)");
    g.addColorStop(1, "rgba(99,217,176,0)");
    s.fillStyle = g; s.fillRect(0, 0, 64, 64);

    function seed() {
      var n = W < 700 ? 22 : 40;
      dots = [];
      for (var i = 0; i < n; i++) {
        dots.push({
          x: Math.random(), y: 0.12 + Math.random() * 0.8,
          r: 7 + Math.random() * 11,
          vx: (Math.random() - 0.5) * 0.012, vy: (Math.random() - 0.5) * 0.008,
          ph: Math.random() * 6.28, sp: 0.6 + Math.random() * 1.4,
          wob: 0.004 + Math.random() * 0.01, mint: Math.random() < 0.22
        });
      }
    }
    function resize() {
      W = canvas.clientWidth; H = canvas.clientHeight;
      canvas.width = Math.round(W * ratio); canvas.height = Math.round(H * ratio);
      if (!dots.length || Math.abs(dots.length - (W < 700 ? 22 : 40)) > 0) seed();
      draw(0, true);
    }
    function draw(t, still) {
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = "lighter";
      var dt = 0;
      if (!still) { dt = Math.min(t - last, 50) / 1000; last = t; }
      for (var i = 0; i < dots.length; i++) {
        var p = dots[i];
        p.ph += dt * p.sp;
        p.x += p.vx * dt + Math.cos(p.ph * 0.7) * p.wob * dt;
        p.y += p.vy * dt + Math.sin(p.ph) * p.wob * dt;
        if (p.x < -0.05) p.x = 1.05; else if (p.x > 1.05) p.x = -0.05;
        if (p.y < 0.08) p.vy = Math.abs(p.vy); else if (p.y > 0.95) p.vy = -Math.abs(p.vy);
        var a = 0.5 + 0.5 * Math.sin(p.ph * 1.7);
        ctx.globalAlpha = 0.15 + 0.85 * a * a;
        var size = p.r * (0.8 + 0.3 * a);
        ctx.drawImage(p.mint ? mint : sprite, p.x * W - size, p.y * H - size, size * 2, size * 2);
      }
      ctx.globalAlpha = 1;
    }
    function loop(t) {
      if (!running) return;
      if (t - last >= STEP) draw(t);
      raf(loop);
    }
    function toggle() {
      var want = heroOn && !reduce.matches && !d.hidden;
      canvas.style.visibility = reduce.matches ? "hidden" : "";
      if (want && !running) { running = true; last = performance.now(); raf(loop); }
      else if (!want) running = false;
    }
    api.resize = resize; api.toggle = toggle;
    resize();
    toggle();
    d.addEventListener("visibilitychange", toggle);
    return api;
  })();

  /* ---- pause the hero (and other animated pictures) while off screen ---- */
  if (hasIO) {
    var playIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        e.target.classList.toggle("is-playing", e.isIntersecting);
        if (e.target === hero) {
          heroOn = e.isIntersecting;
          hero.classList.toggle("is-paused", !heroOn);
          fireflies.toggle();
          if (heroOn) queue();
        }
      });
    }, { rootMargin: "60px 0px" });
    Array.prototype.forEach.call(d.querySelectorAll("[data-play]"), function (el) { playIO.observe(el); });
  } else {
    Array.prototype.forEach.call(d.querySelectorAll("[data-play]"), function (el) { el.classList.add("is-playing"); });
  }

  /* ---- reveal on scroll, with staggered children ---- */
  var reveals = d.querySelectorAll("[data-reveal],[data-stagger]");
  Array.prototype.forEach.call(d.querySelectorAll("[data-stagger]"), function (box) {
    Array.prototype.forEach.call(box.children, function (c, i) { c.style.setProperty("--i", Math.min(i, 10)); });
  });
  if (hasIO && !reduce.matches) {
    var revealIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("is-in"); revealIO.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    Array.prototype.forEach.call(reveals, function (el) { revealIO.observe(el); });
  } else {
    Array.prototype.forEach.call(reveals, function (el) { el.classList.add("is-in"); });
  }
  // the inline script in <head> takes the .js class away again if this never ran
  w.__tsmp = 1;

  /* ---- cards: a soft light follows the pointer ---- */
  Array.prototype.forEach.call(d.querySelectorAll("[data-tilt]"), function (el) {
    el.addEventListener("pointermove", function (e) {
      if (!fine.matches || reduce.matches) return;
      var r = el.getBoundingClientRect();
      el.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
      el.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
    }, passive);
  });

  /* ---- typed invite: the caret width follows the text length ---- */
  Array.prototype.forEach.call(d.querySelectorAll(".typed"), function (el) {
    el.style.setProperty("--n", el.textContent.length);
  });

  /* ---- YouTube: a picture until pressed, then the player ---- */
  Array.prototype.forEach.call(d.querySelectorAll("img[data-fallback]"), function (img) {
    function check() { if (img.naturalWidth && img.naturalWidth < 200) img.src = img.getAttribute("data-fallback"); }
    if (img.complete) check(); else img.addEventListener("load", check);
    img.addEventListener("error", function () { img.src = img.getAttribute("data-fallback"); });
  });
  // without JS each facade is a plain link to YouTube; with it, a button that plays here
  Array.prototype.forEach.call(d.querySelectorAll("a[data-yt]"), function (a) {
    a.setAttribute("role", "button");
    a.addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Spacebar") { e.preventDefault(); play(a); }
    });
  });
  d.addEventListener("click", function (e) {
    var a = e.target.closest && e.target.closest("a[data-yt]");
    if (!a || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    play(a);
  });
  function play(a) {
    var id = a.getAttribute("data-yt");
    var box = d.createElement("div");
    box.className = a.className + " is-playing";
    var f = d.createElement("iframe");
    f.src = "https://www.youtube-nocookie.com/embed/" + encodeURIComponent(id) + "?autoplay=1&rel=0";
    f.title = a.getAttribute("data-title") || "YouTube";
    f.allow = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share";
    f.setAttribute("allowfullscreen", "");
    f.referrerPolicy = "strict-origin-when-cross-origin";
    box.appendChild(f);
    a.parentNode.replaceChild(box, a);
    f.focus();
  }

  /* ---- latest release: version, size and date, from GitHub ---- */
  var rel = d.querySelectorAll("[data-release]");
  if (rel.length && w.fetch) {
    var lang = (root.lang || "en").toLowerCase();
    var locale = lang.indexOf("ru") === 0 ? "ru-RU" : "en-GB";
    fetch("https://api.github.com/repos/Rxflex/TSMP/releases/latest")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var exe = (data.assets || []).filter(function (a) { return a.name === "tsmp.exe"; })[0];
        Array.prototype.forEach.call(rel, function (el) {
          var parts = [];
          if (data.tag_name) parts.push((el.getAttribute("data-label") || "Version") + " " + data.tag_name.replace(/^v/, ""));
          if (exe) parts.push((exe.size / 1048576).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + " " + (el.getAttribute("data-unit") || "MB"));
          if (data.published_at) parts.push(new Date(data.published_at).toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" }));
          if (parts.length) {
            el.textContent = (el.getAttribute("data-prefix") || "") + parts.join(" · ") + (el.getAttribute("data-suffix") || "");
            el.classList.add("is-filled");
          }
        });
      })
      .catch(function () {});
  }
})();
