// The demo video in the hero. It starts only here, so a device that asks for less motion keeps the still poster
// until the person presses Play, and anything that moves can be paused.
(function () {
  const video = document.getElementById("demo");
  const toggle = document.getElementById("demoToggle");
  if (!video || !toggle) return;
  const label = () => { toggle.textContent = video.paused ? "Play" : "Pause"; };
  video.addEventListener("play", label);
  video.addEventListener("pause", label);
  toggle.addEventListener("click", () => { if (video.paused) video.play(); else video.pause(); });
  toggle.hidden = false;
  label();
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) video.play().catch(label);
})();
