/* 냅냥 (낮잠 자는 삼색 고양이) SVG. 시안 v3(napi-look-v3.html)에서 그대로 옮겼다.
 * 전역 NapiCat:
 *   full({animated, scanning}) -> svg 문자열 (방석 위 낮잠 냅냥 전체)
 *   face(status) -> svg 문자열 (상태별 표정 아이콘)
 *   ensureDefs(doc) -> 공유 <defs>(#catHead, #eyesSleep, #mouthW)를 문서에 한 번 넣는다
 * 반환하는 svg 문자열에는 외부 데이터가 들어가지 않는다 (고정 그림만).
 */
(function (root) {
  "use strict";

  var HEAD_PATH = "M46 118 C46 80 70 68 100 68 C130 68 154 80 154 118 C154 148 132 160 100 160 C68 160 46 148 46 118 Z";

  var DEFS =
    '<svg id="napi-cat-defs" width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">' +
    "<defs>" +
    '<clipPath id="headClip"><path d="' + HEAD_PATH + '"/></clipPath>' +
    '<g id="catHead">' +
    '<path d="M58 96 L52 54 Q53 45 61 49 L92 74 Z" fill="#f6a54c" stroke="#5a3d2b" stroke-width="4" stroke-linejoin="round"/>' +
    '<path d="M62 86 L59 61 L80 76 Z" fill="#ffc6c9"/>' +
    '<path d="M108 74 L139 49 Q147 45 148 54 L142 96 Z" fill="#fffaf3" stroke="#5a3d2b" stroke-width="4" stroke-linejoin="round"/>' +
    '<path d="M120 76 L141 61 L138 86 Z" fill="#ffc6c9"/>' +
    '<path d="' + HEAD_PATH + '" fill="#fffaf3"/>' +
    '<g clip-path="url(#headClip)">' +
    '<ellipse cx="62" cy="76" rx="30" ry="22" fill="#f6a54c"/>' +
    '<ellipse cx="128" cy="70" rx="10" ry="7" fill="#f6a54c" opacity=".9"/>' +
    "</g>" +
    '<path d="' + HEAD_PATH + '" fill="none" stroke="#5a3d2b" stroke-width="4"/>' +
    '<ellipse cx="66" cy="132" rx="10" ry="6" fill="#ff9eb0" opacity=".6"/>' +
    '<ellipse cx="134" cy="132" rx="10" ry="6" fill="#ff9eb0" opacity=".6"/>' +
    '<ellipse cx="100" cy="125" rx="3.4" ry="2.4" fill="#ff8fa3"/>' +
    "</g>" +
    '<g id="eyesSleep" fill="none" stroke="#5a3d2b" stroke-width="4" stroke-linecap="round">' +
    '<path d="M68 116 q9 8 18 0"/><path d="M114 116 q9 8 18 0"/>' +
    "</g>" +
    '<path id="mouthW" d="M93 130 q3.5 4 7 0 q3.5 4 7 0" fill="none" stroke="#5a3d2b" stroke-width="3" stroke-linecap="round"/>' +
    "</defs></svg>";

  var EYES_OPEN =
    '<ellipse cx="77" cy="114" rx="6" ry="7.5" fill="#5a3d2b"/><circle cx="79" cy="111" r="2.2" fill="#fff"/>' +
    '<ellipse cx="123" cy="114" rx="6" ry="7.5" fill="#5a3d2b"/><circle cx="125" cy="111" r="2.2" fill="#fff"/>';

  function full(opts) {
    opts = opts || {};
    var cls = "napnyang" + (opts.animated ? " anim" : "") + (opts.scanning ? " scanning" : "");
    return (
      '<svg class="' + cls + '" viewBox="10 20 230 190" role="img" aria-label="방석 위에서 낮잠 자는 고양이 냅냥">' +
      // cushion
      '<rect x="26" y="160" width="196" height="38" rx="19" fill="#c9c2ff" stroke="#5a3d2b" stroke-width="4"/>' +
      '<path d="M44 172 q80 10 160 0" stroke="#b1a8fb" stroke-width="4" fill="none" stroke-linecap="round"/>' +
      // tail
      '<g class="tail">' +
      '<path d="M196 156 C226 150 226 116 204 112" stroke="#5a3d2b" stroke-width="18" fill="none" stroke-linecap="round"/>' +
      '<path d="M196 156 C226 150 226 116 204 112" stroke="#fffaf3" stroke-width="10" fill="none" stroke-linecap="round"/>' +
      '<circle cx="205" cy="113" r="6" fill="#f6a54c"/>' +
      "</g>" +
      '<g class="breathe">' +
      // body loaf
      '<ellipse cx="150" cy="140" rx="58" ry="32" fill="#fffaf3" stroke="#5a3d2b" stroke-width="4"/>' +
      '<path d="M150 109 q26 -2 40 14 q-20 6 -36 0 q-10 -6 -4 -14z" fill="#f6a54c"/>' +
      // head (tilted, resting)
      '<g class="head" transform="rotate(-6 100 120) translate(-4 6)">' +
      '<use href="#catHead"/>' +
      '<g class="eyes-sleep"><use href="#eyesSleep"/></g>' +
      '<g class="eyes-open">' + EYES_OPEN + "</g>" +
      '<use href="#mouthW"/>' +
      "</g>" +
      "</g>" +
      // paws
      '<ellipse cx="80" cy="166" rx="15" ry="10" fill="#fffaf3" stroke="#5a3d2b" stroke-width="4"/>' +
      '<ellipse cx="114" cy="167" rx="15" ry="10" fill="#fffaf3" stroke="#5a3d2b" stroke-width="4"/>' +
      '<path d="M76 162 v5 M84 162 v5 M110 163 v5 M118 163 v5" stroke="#5a3d2b" stroke-width="2.5" stroke-linecap="round"/>' +
      // snot bubble
      '<g class="snot">' +
      '<circle cx="124" cy="128" r="11" fill="#e8f4ff" stroke="#9fc7ef" stroke-width="2.5" opacity=".95"/>' +
      '<circle cx="120" cy="124" r="3" fill="#fff"/>' +
      "</g>" +
      // zzz
      '<g class="zs" font-family="Arial Rounded MT Bold,Arial,sans-serif" font-weight="900">' +
      '<text class="z z1" x="150" y="70" font-size="28" fill="#7b6cf6">Z</text>' +
      '<text class="z z2" x="176" y="50" font-size="20" fill="#9d92f8">z</text>' +
      '<text class="z z3" x="194" y="36" font-size="14" fill="#bdb5fb">z</text>' +
      "</g>" +
      // scanning: magnifier
      '<g class="magnifier">' +
      '<circle cx="176" cy="64" r="16" fill="#eaf6ff" stroke="#5a3d2b" stroke-width="4"/>' +
      '<path d="M188 76 l14 14" stroke="#5a3d2b" stroke-width="7" stroke-linecap="round"/>' +
      "</g>" +
      "</svg>"
    );
  }

  var FACES = {
    awake: { box: "40 40 124 128", body: '<use href="#catHead"/>' + EYES_OPEN + '<use href="#mouthW"/>' },
    drowsy: {
      box: "40 40 124 128",
      body:
        '<use href="#catHead"/>' +
        '<path d="M68 116 h18 M114 116 h18" stroke="#5a3d2b" stroke-width="4" stroke-linecap="round"/>' +
        '<path d="M71 117 q6 5 12 0 M117 117 q6 5 12 0" fill="#5a3d2b"/>' +
        '<use href="#mouthW"/>' +
        '<text x="142" y="66" font-size="18" font-weight="900" fill="#d9a400">z</text>'
    },
    asleep: {
      box: "40 40 124 128",
      body:
        '<use href="#catHead"/><use href="#eyesSleep"/><use href="#mouthW"/>' +
        '<circle cx="124" cy="128" r="9" fill="#e8f4ff" stroke="#9fc7ef" stroke-width="2.5"/>' +
        '<text x="138" y="66" font-size="22" font-weight="900" fill="#7b6cf6">Z</text>'
    },
    rip: {
      box: "40 30 124 138",
      body:
        '<ellipse cx="100" cy="46" rx="26" ry="7" fill="none" stroke="#f5c542" stroke-width="5"/>' +
        '<g opacity=".55"><use href="#catHead"/><use href="#eyesSleep"/><use href="#mouthW"/></g>'
    },
    sleeptalk: {
      box: "40 40 124 128",
      body:
        '<use href="#catHead"/><use href="#eyesSleep"/>' +
        '<ellipse cx="100" cy="134" rx="4.5" ry="5.5" fill="#5a3d2b"/>' +
        '<rect x="128" y="50" width="34" height="24" rx="10" fill="#fff1e8" stroke="#e8590c" stroke-width="2.5"/>' +
        '<text x="145" y="68" font-size="15" font-weight="900" fill="#e8590c" text-anchor="middle">!?</text>'
    },
    ghost: {
      box: "40 40 124 128",
      body:
        '<g opacity=".35"><use href="#catHead"/></g>' +
        '<path d="' + HEAD_PATH + '" fill="none" stroke="#0ea5b7" stroke-width="3" stroke-dasharray="6 6"/>' +
        '<ellipse cx="77" cy="114" rx="6" ry="7.5" fill="#0ea5b7"/><ellipse cx="123" cy="114" rx="6" ry="7.5" fill="#0ea5b7"/>' +
        '<text x="100" y="146" font-size="16" font-weight="900" fill="#0ea5b7" text-anchor="middle">?</text>'
    },
    // 외부 탐색 시도: 돋보기 든 깨어있음 얼굴
    probe: {
      box: "40 40 124 128",
      body:
        '<use href="#catHead"/>' + EYES_OPEN + '<use href="#mouthW"/>' +
        '<circle cx="138" cy="140" r="14" fill="#eaf6ff" stroke="#5a3d2b" stroke-width="4"/>' +
        '<path d="M148 150 l10 10" stroke="#5a3d2b" stroke-width="6" stroke-linecap="round"/>'
    }
  };

  function face(status) {
    var f = FACES[status] || FACES.asleep;
    return '<svg class="napi-face" viewBox="' + f.box + '" aria-hidden="true" focusable="false">' + f.body + "</svg>";
  }

  function ensureDefs(doc) {
    doc = doc || root.document;
    if (!doc || !doc.body || doc.getElementById("napi-cat-defs")) return;
    var holder = doc.createElement("div");
    holder.innerHTML = DEFS; // 고정 문자열 (데이터 없음)
    doc.body.insertBefore(holder.firstChild, doc.body.firstChild);
  }

  root.NapiCat = { full: full, face: face, ensureDefs: ensureDefs, STATUSES: Object.keys(FACES) };

  if (root.document) {
    if (root.document.body) ensureDefs(root.document);
    else root.document.addEventListener("DOMContentLoaded", function () { ensureDefs(root.document); });
  }
})(window);
