// Download buttons from the latest GitHub release. Before the first release, point to the source instead.
(function () {
  "use strict";
  const REPO = "valtomas/para-building-regs";
  const box = document.getElementById("downloads");
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
  const isWin = /Win/i.test(navigator.platform || navigator.userAgent);
  const isLinux = !isMac && !isWin && /Linux|X11/i.test(navigator.platform || navigator.userAgent);

  function link(href, label, sub, primary) {
    const a = document.createElement("a");
    a.className = "btn" + (primary ? " primary" : "");
    a.href = href;
    a.textContent = label + " ";
    if (sub) {
      const s = document.createElement("small");
      s.textContent = sub;
      a.appendChild(s);
    }
    return a;
  }

  function noRelease() {
    box.textContent = "";
    const p = document.createElement("p");
    p.className = "fine";
    p.textContent = "The first release is on its way. Until then you can run Para from source with Python 3.10 or newer.";
    box.appendChild(p);
    box.appendChild(link("https://github.com/" + REPO + "#run-from-source", "Run from source", "", true));
  }

  fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rel) => {
      const assets = Object.fromEntries((rel.assets || []).map((a) => [a.name, a]));
      const mac = assets["Para-mac.zip"], win = assets["Para-windows.zip"], lin = assets["Para-linux.tar.gz"];
      if (!mac && !win && !lin) return noRelease();
      box.textContent = "";
      const mb = (a) => Math.round(a.size / 2 ** 20) + " MB";
      if (mac) box.appendChild(link(mac.browser_download_url, "Download for Mac", mb(mac), isMac || (!isWin && !isLinux)));
      if (win) box.appendChild(link(win.browser_download_url, "Download for Windows", mb(win), isWin));
      if (lin) box.appendChild(link(lin.browser_download_url, "Download for Linux", mb(lin), isLinux));
      const v = document.createElement("p");
      v.className = "fine";
      v.textContent = "Version " + String(rel.tag_name || "").replace(/^v/, "") + ". ";
      const notes = document.createElement("a");
      notes.href = rel.html_url;
      notes.textContent = "What's new";
      v.appendChild(notes);
      box.appendChild(v);
      const hero = document.querySelector("#heroActions .btn.primary");
      const mine = isWin ? win : isLinux ? lin : mac;
      const label = isWin ? "Download for Windows" : isLinux ? "Download for Linux" : "Download for Mac";
      if (hero && mine) { hero.href = mine.browser_download_url; hero.textContent = label; }
    })
    .catch(noRelease);
})();
