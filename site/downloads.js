// Download buttons from the latest GitHub release. Before the first release, point to the source instead.
(function () {
  "use strict";
  const REPO = "valtomas/para-building-regs";
  const box = document.getElementById("downloads");
  const isMac = /Mac/i.test(navigator.platform || navigator.userAgent);
  const isWin = /Win/i.test(navigator.platform || navigator.userAgent);
  const isLinux = !isMac && !isWin && /Linux|X11/i.test(navigator.platform || navigator.userAgent);

  function link(href, label, primary) {
    const a = document.createElement("a");
    a.className = "btn" + (primary ? " primary" : "");
    a.href = href;
    a.textContent = label;
    return a;
  }

  // What the visitor will meet on first run, said beside the buttons that download the app, since most people
  // click a button and never open the install notes below it.
  const FIRST_RUN = isWin
    ? "Windows will show a blue box, \u201cWindows protected your PC\u201d. Click More info, then Run anyway. It says this " +
      "about apps it doesn\u2019t yet recognise, not because anything is wrong, and only when you install."
    : isMac
    ? "The first time you open Para, macOS says it can\u2019t verify it. Open System Settings \u203a Privacy & Security, " +
      "scroll down and click Open Anyway. You only do this once."
    : "";

  function firstRun() {
    const p = document.createElement("p");
    p.className = "first-run";
    p.textContent = FIRST_RUN;
    return p;
  }

  function noRelease() {
    box.textContent = "";
    const p = document.createElement("p");
    p.className = "fine";
    p.textContent = "The first release is on its way. Until then you can run Para from source with Python 3.10 or newer.";
    box.appendChild(p);
    box.appendChild(link("https://github.com/" + REPO + "#run-from-source", "Run from source", true));
  }

  fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rel) => {
      const assets = Object.fromEntries((rel.assets || []).map((a) => [a.name, a]));
      // The installers, or the zip and tarball where a release has none.
      const mac = assets["Para-mac.zip"];
      const win = assets["Para-windows-setup.exe"] || assets["Para-windows.zip"];
      const lin = assets["Para-linux.deb"] || assets["Para-linux.tar.gz"];
      const appimage = assets["Para-linux.AppImage"];
      if (!mac && !win && !lin) return noRelease();
      box.textContent = "";
      if (mac) box.appendChild(link(mac.browser_download_url, "Download for Mac", isMac || (!isWin && !isLinux)));
      if (win) box.appendChild(link(win.browser_download_url, "Download for Windows", isWin));
      if (lin) box.appendChild(link(lin.browser_download_url, appimage ? "Ubuntu and Debian" : "Download for Linux", isLinux));
      if (appimage) box.appendChild(link(appimage.browser_download_url, "Other Linux (AppImage)", false));
      const v = document.createElement("p");
      v.className = "fine";
      v.textContent = "Version " + String(rel.tag_name || "").replace(/^v/, "") + ". ";
      const notes = document.createElement("a");
      notes.href = rel.html_url;
      notes.textContent = "What's new";
      v.appendChild(notes);
      box.appendChild(v);
      // The button at the top of the page names the visitor's system but still leads here, to the download
      // section, where the first-run step is said beside the buttons.
      const hero = document.querySelector("#heroActions .btn.primary");
      const mine = isWin ? win : isLinux ? lin : mac;
      const label = isWin ? "Download for Windows" : isLinux ? "Download for Linux" : "Download for Mac";
      if (hero && mine) hero.textContent = label;
      if (FIRST_RUN && (isWin ? win : mac)) box.after(firstRun());
    })
    .catch(noRelease);
})();
