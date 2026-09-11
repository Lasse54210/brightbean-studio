/* Instagram media specs, browser side (Blue Monkey Media fork).
 *
 * A port of apps/composer/instagram_specs.py. That file is the source of
 * truth: it carries the provenance of every number and it is what stops a
 * save. This copy exists so the composer can say "this 16:9 video will be
 * cropped" while you are still choosing, instead of after you schedule.
 * Change one, change the other; apps/composer/tests/test_instagram_specs.py
 * pins the numbers on the Python side. The mirror is check(): same inputs,
 * same findings, same wording. checkPost() below has no Python twin on
 * purpose. The server only needs the first blocking message, while the panel
 * shows every warning, and two carousel items with the same crop warning need
 * the "File N:" prefix to stay distinguishable (and to keep Alpine's x-for
 * keys unique).
 *
 * It also measures the attached media, and it does that from the DOM rather
 * than from anything the server rendered. Two reasons, and the second is the
 * one that matters:
 *
 *  - A <video> reports videoWidth/videoHeight/duration off the actual file,
 *    and an <img> reports naturalWidth/naturalHeight. Nothing has to be
 *    plumbed through a template for that.
 *  - MediaAsset.width/height/duration are filled in by a background ffprobe
 *    task, so a file that was just uploaded measures 0 on the server for as
 *    long as that worker takes. The browser knows the answer immediately --
 *    it has the file. Reading the server's copy would mean the warning shows
 *    up only after a page reload, which is exactly when it is no longer
 *    useful.
 *
 * The measurements land in an Alpine store rather than in per-panel listeners:
 * the placement panel is rendered once per selected account inside an x-for,
 * and a listener per instance would leak every time an account is toggled.
 */
(function () {
    'use strict';

    var NINE_BY_SIXTEEN = 9 / 16;

    /* How close a ratio has to be before it counts as the recommended one.
       1080x1920 is exactly 9:16; a trimmed or scaled file lands a hair off and
       warning about that would be noise. */
    var RATIO_TOLERANCE = 0.015;

    var MEDIA_SPECS = {
        reel: {
            label: 'A Reel',
            requiredRatio: [0.01, 10.0],
            recommendedRatio: NINE_BY_SIXTEEN,
            duration: [3.0, 15 * 60.0],
        },
        story_video: {
            label: 'A Story video',
            requiredRatio: [0.1, 10.0],
            recommendedRatio: NINE_BY_SIXTEEN,
            duration: [3.0, 60.0],
        },
        story_image: {
            label: 'A Story image',
            requiredRatio: null,
            recommendedRatio: NINE_BY_SIXTEEN,
            duration: null,
        },
        feed_image: {
            label: 'A feed image',
            requiredRatio: null,
            recommendedRatio: [4 / 5, 1.91],
            duration: null,
        },
    };

    var NAMED_RATIOS = [
        ['9:16', 9 / 16],
        ['2:3', 2 / 3],
        ['3:4', 3 / 4],
        ['4:5', 4 / 5],
        ['1:1', 1.0],
        ['5:4', 5 / 4],
        ['4:3', 4 / 3],
        ['3:2', 3 / 2],
        ['16:9', 16 / 9],
        ['1.91:1', 1.91],
    ];

    function specFor(placement, kind) {
        if (placement === 'reel') return MEDIA_SPECS.reel;
        if (placement === 'story') return kind === 'image' ? MEDIA_SPECS.story_image : MEDIA_SPECS.story_video;
        if (placement === 'image' || placement === 'carousel') return MEDIA_SPECS.feed_image;
        return null;
    }

    function formatRatio(ratio) {
        for (var i = 0; i < NAMED_RATIOS.length; i++) {
            if (Math.abs(ratio - NAMED_RATIOS[i][1]) <= NAMED_RATIOS[i][1] * RATIO_TOLERANCE) {
                return NAMED_RATIOS[i][0];
            }
        }
        if (ratio >= 1) return ratio.toFixed(2) + ':1';
        return '1:' + (1 / ratio).toFixed(2);
    }

    function formatDuration(seconds) {
        seconds = Math.round(seconds);
        if (seconds < 60) return seconds + ' s';
        var minutes = Math.floor(seconds / 60);
        var rest = seconds % 60;
        return rest === 0 ? minutes + ' min' : minutes + ' min ' + rest + ' s';
    }

    function ratioFindings(width, height, spec) {
        if (!width || !height || width < 0 || height < 0) return [];

        var ratio = width / height;
        var said = formatRatio(ratio);
        var required = spec.requiredRatio;

        if (required && (ratio < required[0] || ratio > required[1])) {
            return [{
                level: 'block',
                message: spec.label + ' has to be between ' + formatRatio(required[0]) + ' and ' +
                    formatRatio(required[1]) + '. This file is ' + said + ', which Instagram will not accept.',
            }];
        }

        var recommended = spec.recommendedRatio;
        if (Array.isArray(recommended)) {
            if (ratio < recommended[0] || ratio > recommended[1]) {
                return [{
                    level: 'warn',
                    message: 'This file is ' + said + '. ' + spec.label + ' is cropped to fit between ' +
                        formatRatio(recommended[0]) + ' and ' + formatRatio(recommended[1]) + '.',
                }];
            }
            return [];
        }

        if (Math.abs(ratio - recommended) > recommended * RATIO_TOLERANCE) {
            return [{
                level: 'warn',
                message: 'This file is ' + said + '. ' + spec.label + ' is ' + formatRatio(recommended) +
                    ', so Instagram crops the middle ' + formatRatio(recommended) + ' out of it.',
            }];
        }
        return [];
    }

    function durationFindings(duration, spec) {
        if (!spec.duration || !duration || duration < 0) return [];

        var shortest = spec.duration[0];
        var longest = spec.duration[1];
        if (duration < shortest) {
            return [{
                level: 'block',
                message: spec.label + ' is ' + formatDuration(shortest) + ' at the very least. This one is ' +
                    formatDuration(duration) + '.',
            }];
        }
        if (duration > longest) {
            return [{
                level: 'block',
                message: spec.label + ' is ' + formatDuration(longest) + ' at most. This one is ' +
                    formatDuration(duration) + '. Trim it in the media library before publishing.',
            }];
        }
        return [];
    }

    /* Findings for one file at one placement. A zero, null or negative
       measurement is unknown and produces nothing -- see the note above about
       files that have not been measured yet. */
    function check(width, height, duration, placement, kind) {
        var spec = specFor(placement, kind);
        if (!spec) return [];
        return ratioFindings(width, height, spec).concat(durationFindings(duration, spec));
    }

    /* Findings for a whole post at one placement. Only a carousel publishes
       more than one file, and there each item is its own container, so each
       one is judged and named. */
    function checkPost(items, placement) {
        var candidates = placement === 'carousel' ? items : items.slice(0, 1);
        var findings = [];
        candidates.forEach(function (item, index) {
            check(item.width, item.height, item.duration, placement, item.kind).forEach(function (finding) {
                findings.push({
                    level: finding.level,
                    message: placement === 'carousel'
                        ? 'File ' + (index + 1) + ': ' + finding.message
                        : finding.message,
                });
            });
        });
        return findings;
    }

    // ------------------------------------------------------------------
    // Measuring
    // ------------------------------------------------------------------

    function measure() {
        var items = [];
        document.querySelectorAll('#media-list .media-thumb').forEach(function (thumb) {
            var video = thumb.querySelector('video');
            if (video) {
                items.push({
                    kind: 'video',
                    width: video.videoWidth || 0,
                    height: video.videoHeight || 0,
                    /* An unloaded or streaming video reports NaN or Infinity
                       here, and both have to read as "not measured yet". */
                    duration: isFinite(video.duration) ? video.duration : 0,
                });
                return;
            }
            var img = thumb.querySelector('img');
            items.push({
                kind: 'image',
                width: img ? img.naturalWidth || 0 : 0,
                height: img ? img.naturalHeight || 0 : 0,
                duration: 0,
            });
        });
        return items;
    }

    var lastSeen = '';

    function publish() {
        var items = measure();
        var serialised = JSON.stringify(items);
        /* Metadata arrives one element at a time and the DOM is observed, so
           this runs far more often than the answer changes. Writing to the
           store every time would re-render the panel on every frame of an
           upload. */
        if (serialised === lastSeen) return;
        lastSeen = serialised;
        if (window.Alpine && window.Alpine.store('bmmMedia')) {
            window.Alpine.store('bmmMedia').items = items;
        }
    }

    document.addEventListener('alpine:init', function () {
        window.Alpine.store('bmmMedia', { items: [] });
        /* Anything measured before this point went nowhere -- there was no
           store to write to yet -- so the cache has to be dropped or the first
           real publish is skipped as a no-op and the panel stays empty. */
        lastSeen = '';
        publish();
    });

    /* previewUpdate fires after an upload or an import, htmx swaps the list
       when media is removed, and the observer catches the rest: a drag to
       reorder, and the placeholder the uploader inserts by hand. */
    document.body.addEventListener('previewUpdate', publish);
    document.body.addEventListener('htmx:afterSwap', function (event) {
        if (event.target && event.target.id === 'media-list') publish();
    });

    /* Neither event bubbles, so they are caught on the way down. This is what
       turns a just-attached <video> into a measurement: the element exists
       before its metadata does. */
    document.addEventListener('loadedmetadata', publish, true);
    document.addEventListener('load', publish, true);

    function observe() {
        var list = document.getElementById('media-list');
        if (!list) return;
        new MutationObserver(publish).observe(list, { childList: true, subtree: true });
        publish();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', observe);
    } else {
        observe();
    }

    window.bmmInstagramSpecs = {
        check: check,
        checkPost: checkPost,
        formatRatio: formatRatio,
        formatDuration: formatDuration,
        MEDIA_SPECS: MEDIA_SPECS,
    };
})();
