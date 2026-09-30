(function () {
  var VERSION = "20260627-3";
  var PROJECT_ID = "8";
  var KEYPOINT_FROM = "pose_keypoints";
  var VISIBILITY_FROM = "visibility";
  var TARGET_TO = "image";
  var KEYPOINT_TYPE = "keypointlabels";
  var CHOICES_TYPE = "choices";
  var VISIBLE_VALUE = "2";
  var OCCLUDED_VALUE = "1";
  var OCCLUDED_TEXT = "\u906e\u6321\u4f46\u53ef\u63a8\u65ad";
  var VISIBLE_TEXT = "\u53ef\u89c1";
  var lastAutoSelectAt = 0;
  var suppressUntil = 0;
  var observer = null;

  window.__catPoseVisibilityAutoSelect = VERSION;
  window.__catPoseVisibilityDefaultPatch = VERSION;

  function normalizedText(element) {
    return (element && element.textContent || "").replace(/\s+/g, " ").trim();
  }

  function ownerWindow(element) {
    return (element && element.ownerDocument && element.ownerDocument.defaultView) || window;
  }

  function isPoseContext(doc) {
    var href = window.location.href;
    var bodyText = normalizedText((doc || document).body);

    return href.indexOf("/projects/" + PROJECT_ID) !== -1 ||
      href.indexOf("project=" + PROJECT_ID) !== -1 ||
      href.indexOf("projects/" + PROJECT_ID) !== -1 ||
      (bodyText.indexOf(OCCLUDED_TEXT) !== -1 && bodyText.indexOf(VISIBLE_TEXT) !== -1);
  }

  function candidateDocuments() {
    var docs = [document];
    var frames = Array.prototype.slice.call(document.querySelectorAll("iframe"));

    for (var i = 0; i < frames.length; i++) {
      try {
        if (frames[i].contentDocument && frames[i].contentDocument.body) {
          docs.push(frames[i].contentDocument);
        }
      } catch (error) {
        // Same-origin frames only.
      }
    }

    return docs;
  }

  function isVisible(element) {
    if (!element || !element.isConnected) return false;

    var style = ownerWindow(element).getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden") return false;

    var rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }

  function choiceText(element) {
    if (!element) return "";

    var label = element.closest && element.closest("label");
    if (label) return normalizedText(label);

    var parent = element.parentElement;
    return normalizedText(parent) || normalizedText(element);
  }

  function findVisibilityGroup(doc) {
    var candidates = Array.prototype.slice.call(
      doc.querySelectorAll("fieldset,[role='group'],[role='radiogroup'],section,div")
    );
    var best = null;
    var bestArea = Infinity;

    for (var i = 0; i < candidates.length; i++) {
      var element = candidates[i];
      if (!isVisible(element)) continue;

      var text = normalizedText(element);
      if (text.indexOf(OCCLUDED_TEXT) === -1 || text.indexOf(VISIBLE_TEXT) === -1) continue;

      var rect = element.getBoundingClientRect();
      var area = rect.width * rect.height;
      if (area > 0 && area < bestArea) {
        best = element;
        bestArea = area;
      }
    }

    return best;
  }

  function findChoice(group, value, needle) {
    var inputs = Array.prototype.slice.call(group.querySelectorAll("input[type='checkbox'],input[type='radio']"));

    for (var i = 0; i < inputs.length; i++) {
      var input = inputs[i];
      var text = choiceText(input);
      if (String(input.value) === value || text.indexOf(needle) !== -1) {
        return {
          element: input.closest("label") || input,
          input: input
        };
      }
    }

    var candidates = Array.prototype.slice.call(
      group.querySelectorAll("label,button,[role='checkbox'],[role='radio'],[role='button'],input,span,div")
    );

    for (var j = 0; j < candidates.length; j++) {
      var element = candidates[j];
      if (!isVisible(element)) continue;

      var elementText = choiceText(element);
      if (elementText.indexOf(needle) === -1 && String(element.getAttribute("value")) !== value) continue;

      var clickable = element.closest("label,button,[role='checkbox'],[role='radio'],[role='button']");
      var nestedInput = null;

      if (element.matches && element.matches("input")) {
        nestedInput = element;
      } else if (clickable) {
        nestedInput = clickable.querySelector("input");
      }

      return {
        element: clickable || element,
        input: nestedInput
      };
    }

    return null;
  }

  function isChoiceSelected(choice) {
    if (!choice) return false;

    if (choice.input && choice.input.checked) return true;
    if (choice.element && choice.element.matches && choice.element.matches("[aria-checked='true'],[aria-pressed='true'],[aria-selected='true']")) return true;
    if (choice.element && choice.element.closest("[aria-checked='true'],[aria-pressed='true'],[aria-selected='true']")) return true;

    var className = choice.element ? String(choice.element.className || "") : "";
    return /(^|[-_\s])(selected|checked|active)([-_\s]|$)/i.test(className);
  }

  function dispatchInputSelection(input) {
    if (!input) return;

    var win = ownerWindow(input);
    var descriptor = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "checked");
    if (descriptor && descriptor.set) {
      descriptor.set.call(input, true);
    } else {
      input.checked = true;
    }

    input.dispatchEvent(new win.Event("input", { bubbles: true }));
    input.dispatchEvent(new win.Event("change", { bubbles: true }));
  }

  function clickChoice(choice) {
    if (!choice) return;

    if (choice.input) {
      if (!choice.input.checked) {
        choice.input.click();
      }
      if (!choice.input.checked) {
        dispatchInputSelection(choice.input);
      }
      return;
    }

    if (choice.element) {
      var win = ownerWindow(choice.element);
      choice.element.dispatchEvent(new win.MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: win }));
      choice.element.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true, cancelable: true, view: win }));
      choice.element.dispatchEvent(new win.MouseEvent("mouseup", { bubbles: true, cancelable: true, view: win }));
      choice.element.click();
    }
  }

  function autoSelectVisible() {
    return;
    if (Date.now() < suppressUntil) return;

    var docs = candidateDocuments();

    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];
      if (!isPoseContext(doc)) continue;

      var group = findVisibilityGroup(doc);
      if (!group) continue;

      var occluded = findChoice(group, OCCLUDED_VALUE, OCCLUDED_TEXT);
      var visible = findChoice(group, VISIBLE_VALUE, VISIBLE_TEXT);
      if (!visible) continue;

      if (isChoiceSelected(occluded) || isChoiceSelected(visible)) continue;
      if (Date.now() - lastAutoSelectAt < 200) continue;

      lastAutoSelectAt = Date.now();
      clickChoice(visible);

      window.setTimeout(function (occludedChoice, visibleChoice) {
        return function () {
          if (!isChoiceSelected(occludedChoice) && !isChoiceSelected(visibleChoice)) {
            clickChoice(visibleChoice);
          }
        };
      }(occluded, visible), 60);
    }
  }

  function isKeypointResult(item) {
    return item &&
      item.from_name === KEYPOINT_FROM &&
      item.type === KEYPOINT_TYPE &&
      item.id &&
      item.value &&
      Array.isArray(item.value.keypointlabels);
  }

  function isVisibilityResult(item) {
    return item &&
      item.from_name === VISIBILITY_FROM &&
      item.type === CHOICES_TYPE &&
      item.id &&
      item.value &&
      Array.isArray(item.value.choices) &&
      item.value.choices.length > 0;
  }

  function makeVisibilityResult(keypoint) {
    var value = {};
    if (keypoint.value && typeof keypoint.value === "object") {
      value.x = keypoint.value.x;
      value.y = keypoint.value.y;
      if (keypoint.value.width !== undefined) {
        value.width = keypoint.value.width;
      }
    }
    value.choices = [VISIBLE_VALUE];

    return {
      id: keypoint.id,
      from_name: VISIBILITY_FROM,
      to_name: keypoint.to_name || TARGET_TO,
      type: CHOICES_TYPE,
      original_width: keypoint.original_width,
      original_height: keypoint.original_height,
      image_rotation: keypoint.image_rotation || 0,
      value: value,
      origin: keypoint.origin || "manual"
    };
  }

  function patchResultsArray(results) {
    var keypointIds = {};
    var visibilityByRegion = {};
    var additions = [];
    var changed = false;

    for (var i = 0; i < results.length; i++) {
      if (isKeypointResult(results[i])) {
        keypointIds[results[i].id] = true;
      }
    }

    for (var orphanIndex = results.length - 1; orphanIndex >= 0; orphanIndex--) {
      if (isVisibilityResult(results[orphanIndex]) && !keypointIds[results[orphanIndex].id]) {
        results.splice(orphanIndex, 1);
        changed = true;
      }
    }

    for (var v = 0; v < results.length; v++) {
      if (isVisibilityResult(results[v])) {
        visibilityByRegion[results[v].id] = true;
      }
    }

    for (var j = 0; j < results.length; j++) {
      var item = results[j];
      if (!isKeypointResult(item)) continue;
      if (visibilityByRegion[item.id]) continue;

      visibilityByRegion[item.id] = true;
      additions.push(makeVisibilityResult(item));
    }

    for (var k = 0; k < additions.length; k++) {
      results.push(additions[k]);
    }

    return changed || additions.length > 0;
  }

  function patchPayloadObject(value, seen, depth) {
    if (!value || typeof value !== "object" || depth > 8) return false;
    if (seen.indexOf(value) !== -1) return false;
    seen.push(value);

    var changed = false;

    if (Array.isArray(value.result)) {
      changed = patchResultsArray(value.result) || changed;
    }

    if (Array.isArray(value.results)) {
      changed = patchResultsArray(value.results) || changed;
    }

    if (Array.isArray(value)) {
      for (var i = 0; i < value.length; i++) {
        changed = patchPayloadObject(value[i], seen, depth + 1) || changed;
      }
      return changed;
    }

    for (var key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      changed = patchPayloadObject(value[key], seen, depth + 1) || changed;
    }

    return changed;
  }

  function patchJsonString(body) {
    if (typeof body !== "string") return body;

    var trimmed = body.trim();
    if (trimmed.charAt(0) !== "{" && trimmed.charAt(0) !== "[") return body;

    try {
      var parsed = JSON.parse(body);
      if (patchPayloadObject(parsed, [], 0)) {
        return JSON.stringify(parsed);
      }
    } catch (error) {
      return body;
    }

    return body;
  }

  function patchFormData(body) {
    if (!window.FormData || !(body instanceof window.FormData)) return body;

    var keys = ["annotation", "draft", "result", "results", "data"];
    for (var i = 0; i < keys.length; i++) {
      var value = body.get(keys[i]);
      if (typeof value !== "string") continue;

      var patched = patchJsonString(value);
      if (patched !== value) {
        body.set(keys[i], patched);
      }
    }

    return body;
  }

  function patchRequestBody(body) {
    if (typeof body === "string") return patchJsonString(body);
    return patchFormData(body);
  }

  function shouldPatchRequest(url, method) {
    if (!isPoseContext(document)) return false;

    var target = String(url || "");
    var requestMethod = String(method || "GET").toUpperCase();

    if (requestMethod === "GET" || requestMethod === "HEAD" || requestMethod === "OPTIONS") return false;
    return target.indexOf("/api/") !== -1 || target.indexOf("/projects/" + PROJECT_ID) !== -1;
  }

  function patchTransport() {
    if (window.__catPoseVisibilityTransportPatched) return;
    window.__catPoseVisibilityTransportPatched = VERSION;

    if (window.fetch) {
      var originalFetch = window.fetch;
      window.fetch = function (input, init) {
        var url = typeof input === "string" ? input : input && input.url;
        var method = init && init.method || input && input.method;

        if (init && init.body && shouldPatchRequest(url, method)) {
          init = Object.assign({}, init, { body: patchRequestBody(init.body) });
        }

        return originalFetch.call(this, input, init);
      };
    }

    var originalOpen = window.XMLHttpRequest && window.XMLHttpRequest.prototype.open;
    var originalSend = window.XMLHttpRequest && window.XMLHttpRequest.prototype.send;

    if (originalOpen && originalSend) {
      window.XMLHttpRequest.prototype.open = function (method, url) {
        this.__catPoseVisibilityMethod = method;
        this.__catPoseVisibilityUrl = url;
        return originalOpen.apply(this, arguments);
      };

      window.XMLHttpRequest.prototype.send = function (body) {
        if (shouldPatchRequest(this.__catPoseVisibilityUrl, this.__catPoseVisibilityMethod)) {
          body = patchRequestBody(body);
        }

        return originalSend.call(this, body);
      };
    }
  }

  function watch() {
    patchTransport();

    if (observer) return;

    observer = new MutationObserver(function () {
      window.requestAnimationFrame(autoSelectVisible);
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });

    window.setInterval(autoSelectVisible, 250);
    autoSelectVisible();
  }

  document.addEventListener("click", function (event) {
    var target = event.target && event.target.closest("label,button,[role='checkbox'],[role='radio'],[role='button'],span,div");
    var text = normalizedText(target);

    if (text.indexOf(OCCLUDED_TEXT) !== -1) {
      suppressUntil = Date.now() + 1500;
    }
  }, true);

  document.addEventListener("keydown", function (event) {
    if (String(event.key || "").toLowerCase() === "z") {
      suppressUntil = Date.now() + 1500;
    }
  }, true);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }
})();
