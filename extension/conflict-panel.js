/* Isolated UI for reviewing the first pending text edit. */
(function (root) {
  "use strict";
  function init(options) {
    var get = options.get, send = options.send, status = options.status;
    var review = null;
    var running = false;
    function report(message, tone) {
      get("conflict-status").textContent = message;
      get("conflict-status").setAttribute("data-state", tone || "");
      status(message, tone);
    }
    function close() { review = null; get("conflict-overlay").hidden = true; get("btn-review-conflict").focus(); }
    get("btn-review-conflict").addEventListener("click", async function () {
      if (running) return;
      running = true;
      report("Reading Drive version…");
      try {
        var response = await send({ type: "pastex-conflict-preview" });
        if (!response || !response.ok) throw new Error((response && response.error) || "Could not review changes.");
        review = response.review;
        get("conflict-name").textContent = review.name;
        get("conflict-base").value = review.base === null ? "Original unavailable" : review.base;
        get("conflict-local").value = review.local;
        get("conflict-remote").value = review.remote;
        get("conflict-overlay").hidden = false;
        get("conflict-close").focus();
        report("Review the pending edit and Drive version.");
      } catch (err) { report(err.message || "Could not review changes.", "error"); }
      finally { running = false; }
    });
    get("conflict-copy-drive").addEventListener("click", async function () {
      if (!review) return;
      try { await options.clipboard.writeText(review.remote); report("Drive text copied. Keep it private.", "ok"); }
      catch (err) { report("Could not copy Drive text.", "error"); }
    });
    get("conflict-keep-drive").addEventListener("click", async function () {
      if (!review || running || !options.confirm("Replace this pending text with the displayed Drive text? Copy your edit first if needed.")) return;
      running = true;
      try {
        var response = await send({ type: "pastex-conflict-keep-drive", review: review });
        if (!response || !response.ok) throw new Error((response && response.error) || "Could not use Drive text.");
        await options.refresh();
        close();
        report("Drive text kept. Sync when ready.", "ok");
      } catch (err) { report(err.message || "Could not use Drive text.", "error"); }
      finally { running = false; }
    });
    get("conflict-keep-local").addEventListener("click", async function () {
      if (!review || running || !options.confirm("Keep the pending edit? It may overwrite the displayed Drive text when synced.")) return;
      running = true;
      try {
        var response = await send({ type: "pastex-conflict-keep-local", review: review });
        if (!response || !response.ok) throw new Error((response && response.error) || "Could not keep the edit.");
        close();
        report("Pending edit kept. Sync when ready.", "ok");
      } catch (err) { report(err.message || "Could not keep the edit.", "error"); }
      finally { running = false; }
    });
    get("conflict-close").addEventListener("click", close);
    get("conflict-overlay").addEventListener("click", function (event) { if (event.target === get("conflict-overlay")) close(); });
  }
  root.PastexConflictPanel = { init: init };
})(window);
