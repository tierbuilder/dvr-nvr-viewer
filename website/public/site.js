// Points the big download button at the right file for this computer. Without JavaScript the
// button just jumps to the list of downloads, which works just as well.
(function () {
  var ua = navigator.userAgent, platform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
  var os = null, label = "";
  if (/android|iphone|ipad|ipod/i.test(ua) || (/Mac/.test(platform) && navigator.maxTouchPoints > 1)) {
    os = "phone";
  } else if (/win/i.test(platform) || /Windows/.test(ua)) {
    os = "windows"; label = "for Windows";
  } else if (/mac/i.test(platform) || /Mac OS X/.test(ua)) {
    os = "mac-arm"; label = "for Mac";
    // Intel Macs report an Intel GPU; Apple silicon reports "Apple"
    try {
      var gl = document.createElement("canvas").getContext("webgl");
      var info = gl && gl.getExtension("WEBGL_debug_renderer_info");
      var gpu = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : "";
      if (/intel|amd|radeon/i.test(gpu)) { os = "mac-intel"; label = "for Intel Mac"; }
    } catch (e) {}
  } else if (/linux/i.test(platform) || /Linux/.test(ua)) {
    os = /aarch64|arm/i.test(platform + ua) ? "pi" : "linux";
    label = os === "pi" ? "for Raspberry Pi" : "for Linux";
  }
  var button = document.getElementById("hero-dl");
  if (!button || !os) return;
  if (os === "phone") {
    button.firstChild.textContent = "Get it for your computer";
    return;
  }
  var row = document.querySelector('.dl[data-os="' + os + '"]');
  if (!row) return;
  row.classList.add("match");
  button.href = row.querySelector("a").getAttribute("href");
  document.getElementById("hero-os").textContent = " " + label;
})();
