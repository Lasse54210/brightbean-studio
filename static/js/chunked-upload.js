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

  // Same lookup as the composer's own upload code, cookie fallback included. The
  // hidden input is not on every page that can upload, and without the fallback
  // the very first request is rejected by CSRF, so the upload never even starts.
  function csrf() {
    var el = document.querySelector("[name=csrfmiddlewaretoken]");
    if (el && el.value) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  // The chunked part endpoints live under the workspace-scoped media URL. Derive
  // that from the current page so this file needs no template variables, and so
  // it also works from the composer, whose own path has no /media/ in it.
  function mediaBase() {
    var m = window.location.pathname.match(/^(\/workspace\/[^/]+\/)/);
    return (m ? m[1] : "/") + "media/";
  }

  function urlFor(path) {
    return mediaBase() + path;
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

  /**
   * Upload one file in parts.
   *
   * `opts.finish` false stops after the last part and hands back the session id,
   * for callers that have their own finish endpoint (the composer attaches the
   * asset to a post and answers with HTML, which the media library does not).
   */
  async function uploadOneChunked(file, folderId, onProgress, opts) {
    opts = opts || {};
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

    if (opts.finish === false) {
      return { ok: true, uploadId: session.upload_id };
    }

    var finished = await postJson(
      "upload/chunked/" + session.upload_id + "/finish/",
      folderId ? { folder_id: folderId } : {}
    );
    if (!finished.ok) {
      return { ok: false, error: await errorText(finished, "Could not finish upload") };
    }
    return { ok: true, uploadId: session.upload_id };
  }

  // ── Composer ────────────────────────────────────────────────────────────
  // The composer has its own upload endpoint that takes the file in one request
  // and answers with a rendered partial. That is the path most people actually
  // use, and it is where a large video is cut off by the proxy: the connection
  // is reset, so the bar fills and then goes red with a Retry button.
  //
  // Same approach as below: wrap the global factory and replace one method, so
  // the template keeps its own markup and progress handling.
  var originalComposer = window.composerApp;
  // One line at load, so "is this file even active" is answered at a glance
  // instead of by another round of guessing.
  console.info(
    "[chunked-upload] actief. composer=" + (typeof originalComposer === "function") +
      ", mediabibliotheek=" + (typeof window.mediaLibrary === "function") +
      ", drempel=" + CHUNKED_FROM_BYTES,
  );
  if (typeof originalComposer === "function") {
    window.composerApp = function () {
      var component = originalComposer.apply(this, arguments);
      var originalUpload = component.uploadFile;

      component.uploadFile = function (file) {
        if (!file || file.size <= CHUNKED_FROM_BYTES) {
          return originalUpload.call(this, file);
        }
        var self = this;

        // Same placeholder and progress ring as the single-request path, built
        // here so the template does not have to change. Keep the markup in step
        // with uploadFile in compose.html if that ever moves.
        var mediaList = document.getElementById("media-list");
        var thumb = document.createElement("div");
        thumb.className = "media-thumb";
        thumb.id = "upload-" + Date.now() + "-" + Math.random().toString(36).slice(2, 7);
        thumb.innerHTML =
          '<div class="w-full h-full bg-stone-100 flex items-center justify-center"></div>' +
          '<div class="upload-overlay"><svg width="28" height="28" viewBox="0 0 36 36">' +
          '<circle cx="18" cy="18" r="14" fill="none" stroke="rgba(255,255,255,0.25)" stroke-width="3"/>' +
          '<circle class="upload-progress-ring" cx="18" cy="18" r="14" fill="none" stroke="white"' +
          ' stroke-width="3" stroke-dasharray="87.96" stroke-dashoffset="87.96"' +
          ' stroke-linecap="round" transform="rotate(-90 18 18)"/></svg></div>';
        if (mediaList) mediaList.appendChild(thumb);

        if (file.type && file.type.indexOf("video/") === 0) {
          var video = document.createElement("video");
          video.src = URL.createObjectURL(file);
          video.className = "w-full h-full object-cover";
          video.muted = true;
          video.preload = "metadata";
          var placeholder = thumb.querySelector(".bg-stone-100");
          if (placeholder) placeholder.replaceWith(video);
        }

        var ring = thumb.querySelector(".upload-progress-ring");
        var circumference = 87.96;

        self.isUploading = true;
        uploadOneChunked(
          file,
          null,
          function (percent) {
            if (ring) ring.style.strokeDashoffset = circumference * (1 - percent / 100);
          },
          { finish: false },
        )
          .then(async function (result) {
            if (!result.ok) throw new Error(result.error || "Upload failed");

            // The post id can appear during the upload (autosave), so read it as
            // late as possible; that is also what uploadUrl does.
            var postId = self.autosavedPostId || null;
            var base = window.location.pathname.replace(/\/compose\/.*$/, "/compose/");
            var url = postId
              ? base + postId + "/upload-media/chunked/" + result.uploadId + "/finish/"
              : base + "upload-media/chunked/" + result.uploadId + "/finish/";

            var res = await fetch(url, {
              method: "POST",
              headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
              body: JSON.stringify({}),
            });
            if (!res.ok) throw new Error(await errorText(res, "Could not finish upload"));

            var html = await res.text();
            thumb.insertAdjacentHTML("afterend", html);
            var inserted = thumb.nextElementSibling;
            thumb.remove();
            if (inserted && window.htmx) window.htmx.process(inserted.parentElement);
            if (file.type && file.type.indexOf("image/") === 0 && self._applyYoutubeThumbnail) {
              self._applyYoutubeThumbnail(
                res.headers.get("X-Uploaded-Asset-Id"),
                res.headers.get("X-Uploaded-Asset-Url"),
              );
            }
            document.body.dispatchEvent(new CustomEvent("previewUpdate"));
          })
          .catch(function (err) {
            // Loudly, and on the thumb itself: a red tile with a Retry button and
            // no reason is what made this take four rounds to diagnose.
            var reden = (err && err.message) || String(err);
            console.error("[chunked-upload] " + file.name + ": " + reden);
            if (self._showUploadError) self._showUploadError(thumb, file);
            if (thumb) thumb.setAttribute("title", reden);
            // Also on screen. The composer already renders formError, and a red
            // tile without a reason is what made this take several rounds.
            try {
              self.formError = "Upload " + file.name + ": " + reden;
            } catch (e) {
              /* not every component has formError */
            }
          })
          .finally(function () {
            self.isUploading = false;
            if (self._processQueue) self._processQueue();
          });
      };

      return component;
    };
  }

  // ── Media library ───────────────────────────────────────────────────────
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
