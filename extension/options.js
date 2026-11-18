/* Pastex Settings page: general options, service-account key, Drive folder. */

(function () {
  "use strict";

  var Store = window.PastexStore;
  var Drive = window.PastexDrive;
  var Native = window.PastexNative;

  // Last native-host status (null when the apt host is absent). Config
  // files win over browser storage; storage is still written as fallback.
  var nativeInfo = null;

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text) {
    $("status").textContent = text;
  }

  function setLine(id, text, kind) {
    var el = $(id);
    el.textContent = text;
    el.classList.remove("ok", "bad");
    if (kind) {
      el.classList.add(kind);
    }
  }

  async function refreshNativeLine() {
    try {
      nativeInfo = await Native.status();
    } catch (err) {
      nativeInfo = null;
    }
    if (!nativeInfo) {
      setLine("native-status", "Config files: unavailable (browser storage is used).", "");
      return;
    }
    var sa = nativeInfo.serviceAccount || {};
    var drive = nativeInfo.drive || {};
    var bits = [
      sa.configured ? "key ✓ " + (sa.email || "") : "key ✗",
      drive.configured ? "folder ✓ " + (drive.folderId || "") : "folder ✗",
    ];
    setLine(
      "native-status",
      "Config files: " + nativeInfo.configDir + " — " + bits.join(", "),
      sa.configured && drive.configured ? "ok" : ""
    );
  }

  async function refreshAll() {
    await refreshNativeLine();
    await refreshKeyLine();
    await refreshFolderLine();
  }

  async function refreshKeyLine() {
    if (nativeInfo && nativeInfo.serviceAccount && nativeInfo.serviceAccount.configured) {
      setLine("sa-status", "Connected as " + nativeInfo.serviceAccount.email + " (from file).", "ok");
      return;
    }
    var sa = await Store.loadServiceAccount().catch(function () {
      return null;
    });
    if (sa) {
      setLine("sa-status", "Connected as " + sa.client_email + " (browser storage).", "ok");
    } else {
      setLine("sa-status", "No key saved.", "");
    }
  }

  async function refreshFolderLine() {
    if (nativeInfo && nativeInfo.drive && nativeInfo.drive.configured) {
      setLine("folder-status", "Folder ID " + nativeInfo.drive.folderId + " (from file).", "ok");
      $("folder-id").value = nativeInfo.drive.folderId;
      return;
    }
    var drive = await Store.loadDrive();
    if (drive.folderId) {
      var label = "Folder ID " + drive.folderId;
      if (drive.folderName) {
        label = "Folder “" + drive.folderName + "” (" + drive.folderId + ")";
      }
      setLine("folder-status", label + " (browser storage).", "ok");
      $("folder-id").value = drive.folderId;
    } else {
      setLine("folder-status", "No folder selected.", "");
    }
  }

  async function resolveServiceAccount() {
    try {
      var config = await Native.readConfig();
      if (config && config.serviceAccount && config.serviceAccount.client_email) {
        return config.serviceAccount;
      }
    } catch (err) {
      /* fall through to browser storage */
    }
    return Store.loadServiceAccount().catch(function () {
      return null;
    });
  }

  async function dualWriteServiceAccount(parsed, raw) {
    // Do not claim success if a file write fails: an older file would still
    // take precedence over the new browser copy.
    if (nativeInfo) {
      await Native.writeServiceAccount(raw);
    }
    await Store.saveServiceAccount(parsed);
  }

  async function dualWriteDrive(folderId, folderName, folderUrl) {
    if (nativeInfo) {
      await Native.writeDriveConfig(folderId, folderUrl || "");
    }
    var drive = await Store.loadDrive();
    drive.folderId = folderId;
    drive.folderName = folderName || "";
    await Store.saveDrive(drive);
  }

  $("btn-save-key").addEventListener("click", async function () {
    var raw = $("sa-json").value;
    var sa;
    try {
      sa = Drive.parseServiceAccount(raw);
    } catch (err) {
      setLine("sa-status", "Invalid key: " + err.message, "bad");
      return;
    }
    Drive.clearTokenCache();
    var full;
    try {
      full = JSON.parse(raw);
    } catch (err) {
      full = sa;
    }
    try {
      await dualWriteServiceAccount(sa, full);
      $("sa-json").value = "";
      await refreshAll();
      setStatus(nativeInfo ? "Key saved to the config file." : "Key saved to browser storage.");
    } catch (err) {
      setStatus("Could not save the key: " + (err.message || err));
    }
  });

  $("btn-test-key").addEventListener("click", async function () {
    setStatus("Testing…");
    var sa = await resolveServiceAccount();
    if (!sa) {
      setLine("sa-status", "Save a key first.", "bad");
      setStatus("");
      return;
    }
    try {
      var token = await Drive.fetchAccessToken(sa);
      var about = await Drive.aboutUser(token);
      var email = about && about.user ? about.user.emailAddress : sa.client_email;
      setLine("sa-status", "Connected as " + email, "ok");
      setStatus("Connection works.");
    } catch (err) {
      setLine("sa-status", "Connection failed: " + err.message, "bad");
      setStatus("");
    }
  });

  $("btn-clear-key").addEventListener("click", async function () {
    if (!window.confirm("Disconnect the service account? Sync stops until a key is saved again.")) {
      return;
    }
    Drive.clearTokenCache();
    await Store.clearServiceAccount();
    await refreshAll();
    if (nativeInfo && nativeInfo.serviceAccount && nativeInfo.serviceAccount.configured) {
      setStatus("Browser copy cleared; the key file still takes precedence.");
    } else {
      setStatus("Disconnected.");
    }
  });

  $("btn-verify-folder").addEventListener("click", async function () {
    var folderId = $("folder-id").value.trim();
    if (!folderId) {
      setLine("folder-status", "Enter a folder ID first.", "bad");
      return;
    }
    var sa = await resolveServiceAccount();
    if (!sa) {
      setLine("folder-status", "Save a service-account key first.", "bad");
      return;
    }
    setStatus("Verifying…");
    try {
      var token = await Drive.fetchAccessToken(sa);
      var meta = await Drive.driveFetch(token, "/files/" + encodeURIComponent(folderId), {
        query: { fields: "id,name,mimeType" },
      });
      if (meta.mimeType !== Drive.FOLDER_MIME) {
        setLine("folder-status", "That ID is a file, not a folder.", "bad");
        setStatus("");
        return;
      }
      await Drive.listChildren(token, folderId);
      await dualWriteDrive(meta.id, meta.name, "");
      await refreshAll();
      setStatus("Folder verified and saved.");
    } catch (err) {
      var hint = err.message || err;
      if (String(hint).indexOf("404") !== -1) {
        hint += " — is the folder shared with the service account email?";
      }
      setLine("folder-status", "Cannot access that folder: " + hint, "bad");
      setStatus("");
    }
  });

  $("btn-create-folder").addEventListener("click", async function () {
    var sa = await resolveServiceAccount();
    if (!sa) {
      setLine("folder-status", "Save a service-account key first.", "bad");
      return;
    }
    setStatus("Creating…");
    try {
      var token = await Drive.fetchAccessToken(sa);
      var folderId = await Drive.createFolder(token, "Pastex", "root");
      await dualWriteDrive(folderId, "Pastex", "");
      await refreshAll();
      setStatus("Folder created. Its files live in the service account's Drive.");
    } catch (err) {
      setLine("folder-status", "Could not create the folder: " + (err.message || err), "bad");
      setStatus("");
    }
  });

  $("opt-floating-button").addEventListener("change", async function (event) {
    var settings = await Store.loadSettings();
    settings.floatingButton = !!event.target.checked;
    await Store.saveSettings(settings);
    setStatus("Setting saved.");
  });

  $("btn-clear-local").addEventListener("click", async function () {
    if (!window.confirm("Delete ALL local snippets? Drive copies are kept.")) {
      return;
    }
    await Store.saveTree([]);
    setStatus("Local snippets deleted.");
  });

  (async function init() {
    try {
      var settings = await Store.loadSettings();
      $("opt-floating-button").checked = !!settings.floatingButton;
      await refreshAll();
    } catch (err) {
      setStatus("Storage is unavailable: " + (err && err.message ? err.message : err));
    }
  })();
})();
