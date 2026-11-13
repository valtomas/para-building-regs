// The demo video in the hero loops silently. It starts only here, so a device that asks for less motion keeps the
// still poster instead. Wherever it doesn't start by itself (less motion asked for, as Windows does with animation
// effects off or over Remote Desktop, or a browser that won't play unasked), it gets the browser's own controls, so
// it's plainly a video and can be played.
(function () {
  const video = document.getElementById("demo");
  if (!video) return;
  const byHand = () => { video.controls = true; };
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) byHand();
  else video.play().catch(byHand);
})();
