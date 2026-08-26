/*
 * Chunked upload for the media library.
 *
 * Why: a reverse proxy in front of the app can cap a single request body. On
 * Cloudflare's Free and Pro plans that is 100 MB, answered with a 413 the app
 * never sees, so every upload above it fails silently from the app's point of
 * view. The cap is per request, not per file, so splitting the file in the
 * browser removes it.
 *
 * How this stays out of the way of upstream: it does not modify the Alpine
 * component in library_index.html. It wraps the global `mediaLibrary()` factory
 * and replaces one method, `startUpload`, keeping the same queue shape so the
 * existing progress markup keeps working. Files under the threshold keep taking
 * the original single-request path.
 */
(function () {
  "use strict";

  // Below this, one request is fine and cheaper. Comfortably under the 100 MB cap.
  var CHUNKED_FROM_BYTES = 90 * 1024 * 1024;

  // Parallel part transfers. More does not help on a typical office line and
  // costs browser memory, since each part is held while it is in flight.
  var PARALLEL = 3;

  function csrf() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  function urlFor(path) {
    // The library lives under the workspace-scoped media URL; derive the base
    // from the current page so this file needs no template variables.
    var base = window.location.pathname.replace(/\/media\/.*$/, "/media/");
    return base + path;
  }

  function postJson(path, body) {
    return fetch(urlFor(path), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    });
  }

  async function errorText(res, fallback) {
    try {
      var data = await res.json();
      if (data && data.error) return data.error;
    } catch (e) {
      /* not JSON; the status code is all we have */
    }
    return fallback + " (" + res.status + ")";
  }

  // One part, with progress. XMLHttpRequest and not fetch: only upload.onprogress
  // reports how many bytes left the machine, and without it the bar sits still
  // through every 16 MB part.
  function putPart(url, blob, onProgress) {
    return new Promise(function (resolve) {
      var xhr = new XMLHttpRequest();
      xhr.open("PUT", url);
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) onProgress(e.loaded);
      };
      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 300) return resolve({ ok: true });
        resolve({ ok: false, error: "Part upload failed (" + xhr.status + ")" });
      };
      xhr.onerror = function () {
        resolve({ ok: false, error: "Connection lost during upload." });
      };
      xhr.send(blob);
    });
  }

  async function uploadOneChunked(file, folderId, onProgress) {
    var started = await postJson("upload/chunked/", {
      filename: file.name,
      size: file.size,
      content_type: file.type || "application/octet-stream",
    });
    if (!started.ok) {
      return { ok: false, error: await errorText(started, "Could not start upload") };
    }
    var session = await started.json();
    var partSize = session.part_size;
    var partCount = session.part_count;

    // What is already there? Empty on a fresh session; on a retry with the same
    // session this is what makes resuming free.
    var have = [];
    var statusRes = await fetch(
      urlFor("upload/chunked/" + session.upload_id + "/status/"),
      { headers: { "X-CSRFToken": csrf() } }
    );
    if (statusRes.ok) {
      var statusData = await statusRes.json();
      have = statusData.received || [];
    }

    var sentPerPart = {};
    have.forEach(function (n) {
      sentPerPart[n] = n < partCount ? partSize : file.size - (partCount - 1) * partSize;
    });
    function report() {
      var total = 0;
      Object.keys(sentPerPart).forEach(function (k) {
        total += sentPerPart[k];
      });
      onProgress(Math.min(100, Math.round((total / file.size) * 100)));
    }
    report();

    var todo = [];
    for (var n = 1; n <= partCount; n++) {
      if (have.indexOf(n) === -1) todo.push(n);
    }

    // URLs are handed out in batches and expire, so fetch them as we go instead
    // of minting thousands up front that go stale on a slow line.
    var urlCache = {};
    (session.urls || []).forEach(function (u) {
      urlCache[u.part_number] = u.url;
    });

    async function ensureUrls(numbers) {
      var missing = numbers.filter(function (n) {
        return !urlCache[n];
      });
      if (missing.length === 0) return true;
      var res = await postJson("upload/chunked/" + session.upload_id + "/urls/", {
        parts: missing,
      });
      if (!res.ok) return false;
      var data = await res.json();
      (data.urls || []).forEach(function (u) {
        urlCache[u.part_number] = u.url;
      });
      return true;
    }

    var failed = null;
    var index = 0;

    async function worker() {
      while (index < todo.length && !failed) {
        // Capture our own position before anyone else advances the shared
        // cursor. Reading `index` again afterwards is a race: with several
        // workers running, the batch asked for would not necessarily contain our
        // own part number, and then this part had no URL and the whole upload
        // failed near the end.
        var pos = index++;
        var n = todo[pos];

        if (!urlCache[n]) {
          var got = await ensureUrls(todo.slice(pos, pos + 8));
          if (!got || !urlCache[n]) {
            failed = "Could not get an upload URL.";
            return;
          }
        }

        var from = (n - 1) * partSize;
        var to = Math.min(from + partSize, file.size);
        var res = await putPart(urlCache[n], file.slice(from, to), function (bytes) {
          sentPerPart[n] = bytes;
          report();
        });

        if (!res.ok) {
          // One retry with a fresh URL. A signed URL is only valid for a while,
          // and on a slow line a part that starts late can find its URL already
          // expired. Failing the whole upload for that would be a shame when the
          // fix is one request.
          delete urlCache[n];
          sentPerPart[n] = 0;
          report();
          var again = await ensureUrls([n]);
          if (again && urlCache[n]) {
            res = await putPart(urlCache[n], file.slice(from, to), function (bytes) {
              sentPerPart[n] = bytes;
              report();
            });
          }
        }

        if (!res.ok) {
          failed = res.error;
          return;
        }
        sentPerPart[n] = to - from;
        report();
      }
    }

    var workers = [];
    for (var w = 0; w < Math.min(PARALLEL, todo.length); w++) workers.push(worker());
    await Promise.all(workers);

    if (failed) {
      // The session is left open on purpose: the parts that did arrive stay
      // usable for another attempt, and the cleanup task discards it otherwise.
      return { ok: false, error: failed };
    }

    var finished = await postJson(
      "upload/chunked/" + session.upload_id + "/finish/",
      folderId ? { folder_id: folderId } : {}
    );
    if (!finished.ok) {
      return { ok: false, error: await errorText(finished, "Could not finish upload") };
    }
    return { ok: true };
  }

  var original = window.mediaLibrary;
  if (typeof original !== "function") return;

  window.mediaLibrary = function () {
    var component = original.apply(this, arguments);
    var originalStart = component.startUpload;

    component.startUpload = function (files) {
      var big = files.filter(function (f) {
        return f.size > CHUNKED_FROM_BYTES;
      });
      if (big.length === 0) return originalStart.call(this, files);

      // Mixed selections: let the original path handle everything small, so its
      // behaviour is untouched, and take only the big ones here.
      var small = files.filter(function (f) {
        return f.size <= CHUNKED_FROM_BYTES;
      });
      if (small.length > 0) originalStart.call(this, small);

      var self = this;
      self.uploading = true;
      var offset = self.uploadQueue.length;
      big.forEach(function (f) {
        self.uploadQueue.push({
          name: f.name,
          size: f.size,
          progress: 0,
          status: "pending",
          error: null,
          file: f,
        });
      });

      var done = 0;
      big.forEach(function (file, i) {
        var row = offset + i;
        uploadOneChunked(file, self.currentFolder, function (percent) {
          self.uploadQueue[row].progress = percent;
          self.uploadQueue[row].status = "uploading";
        }).then(function (result) {
          done++;
          if (result.ok) {
            self.uploadQueue[row].status = "complete";
            self.uploadQueue[row].progress = 100;
          } else {
            self.uploadQueue[row].status = "error";
            self.uploadQueue[row].error = result.error;
          }
          if (done === big.length) {
            setTimeout(function () {
              self.uploading = false;
              self.uploadQueue = [];
              if (window.htmx) window.htmx.trigger("#asset-grid", "uploadsComplete");
            }, 800);
          }
        });
      });
    };

    return component;
  };
})();
