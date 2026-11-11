// The demo video in the hero loops silently. It starts only here, so a device that asks for less motion keeps the
// still poster instead.
(function () {
  const video = document.getElementById("demo");
  if (video && !matchMedia("(prefers-reduced-motion: reduce)").matches) video.play().catch(() => {});
})();
