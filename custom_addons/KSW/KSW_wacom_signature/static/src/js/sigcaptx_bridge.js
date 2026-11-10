/**
 * Adapter around Wacom's SigCaptX browser bridge for STU signature pads.
 *
 * SigCaptX itself is native Windows software (Wacom Signature SDK +
 * SigCaptX Library) that must be installed separately on every client PC
 * with a pad attached - it exposes a localhost web service that the
 * vendor JS files below talk to. See ../../../README.md for the client
 * setup checklist.
 *
 * The 5 files loaded below are Wacom's own MIT-licensed sample code
 * (https://github.com/Wacom-Developer/sdk-for-signature-sigcaptx-windows),
 * copied verbatim into static/vendor/ - only the LICENCEKEY constant in
 * SigCaptX-Globals.js was filled in (Wacom's public, free "Lite" license
 * from that repo's GETTING-STARTED.md, good for standard capture).
 *
 * This file does not reimplement the capture protocol - it drives the
 * vendor script's own documented entry points (`actionWhenRestarted`,
 * `dynCapt.Capture`, `sigObj.RenderBitmap`) the same way Wacom's own
 * SigCaptX-Capture.html sample does, adapted to return a Promise instead
 * of writing into page DOM elements.
 */

import { loadJS } from "@web/core/assets";

const VENDOR_BASE = "/KSW_wacom_signature/static/vendor/";
// Load order matches <script> order in Wacom's own SigCaptX-Capture.html.
const VENDOR_SCRIPTS = [
    "wgssSigCaptX.js",
    "base64.js",
    "SigCaptX-Utils.js",
    "SigCaptX-SessionControl.js",
    "SigCaptX-Globals.js",
].map((f) => VENDOR_BASE + f);

// The vendor session-detect logic has its own internal 1500ms TIMEOUT
// (see SigCaptX-Globals.js TIMEOUT constant); give it a bit of headroom.
const DETECT_TIMEOUT_MS = 2500;

let vendorLoadPromise = null;
let sessionReadyPromise = null;

function loadVendorScripts() {
    if (!vendorLoadPromise) {
        vendorLoadPromise = (async () => {
            for (const src of VENDOR_SCRIPTS) {
                await loadJS(src);
            }
        })().catch((error) => {
            vendorLoadPromise = null; // allow retry on next call
            throw error;
        });
    }
    return vendorLoadPromise;
}

/**
 * The vendor scripts are classic (non-module) globals written for a real
 * HTML page - actionWhenRestarted() unconditionally touches
 * #imageBox/#txtDisplay via document.getElementById, and print() writes
 * into #txtDisplay. Since our OWL widget has no such page furniture, we
 * inject two elements once so the vendor code doesn't throw.
 *
 * imageBox is positioned off-screen rather than display:none, and sized
 * exactly like Wacom's own SigCaptX-Capture.html sample
 * (height:35mm;width:60mm) - RenderBitmap is called with
 * imageBox.clientWidth/clientHeight, matching that proven-working sample
 * exactly instead of guessing at fixed pixel values (display:none would
 * collapse clientWidth/clientHeight to 0).
 */
function ensureVendorDomHooks() {
    if (document.getElementById("txtDisplay")) {
        return;
    }
    const hidden = document.createElement("div");
    hidden.style.cssText = "position:absolute; left:-9999px; top:-9999px;";
    hidden.innerHTML =
        '<textarea id="txtDisplay"></textarea>' +
        '<div id="imageBox" style="height:35mm;width:60mm;"></div>';
    document.body.appendChild(hidden);
}

/**
 * Boots (or reuses) the SigCaptX session. Resolves true once
 * wgssSignatureSDK/sigCtl/dynCapt are ready, false if the local SigCaptX
 * service could not be detected within the timeout.
 */
function startSession() {
    if (sessionReadyPromise) {
        return sessionReadyPromise;
    }
    sessionReadyPromise = new Promise((resolve) => {
        let settled = false;
        const timer = setTimeout(() => {
            if (!settled) {
                settled = true;
                sessionReadyPromise = null; // allow retry on a later click
                resolve(false);
            }
        }, DETECT_TIMEOUT_MS);

        // actionWhenRestarted (SigCaptX-SessionControl.js) detects the local
        // service, then constructs sigCtl -> sets the licence -> constructs
        // dynCapt -> fetches sigObj -> checks DLL versions, calling its
        // callback only once that whole chain succeeds. It never calls back
        // on failure, hence the timeout race above.
        window.actionWhenRestarted(() => {
            if (!settled) {
                settled = true;
                clearTimeout(timer);
                resolve(true);
            }
        });
    });
    return sessionReadyPromise;
}

/**
 * Loads the vendor bridge (if needed) and starts/reuses a SigCaptX
 * session. Resolves true if a pad capture service is ready to use.
 */
export async function checkWacomBridge() {
    try {
        await loadVendorScripts();
    } catch {
        return false; // vendor files missing from static/vendor/
    }
    ensureVendorDomHooks();
    return startSession();
}

/** Synchronous best-effort check - only meaningful after checkWacomBridge() resolved true. */
export function isWacomBridgeAvailable() {
    return !!(
        window.wgssSignatureSDK &&
        window.wgssSignatureSDK.running &&
        window.dynCapt &&
        window.sigCtl
    );
}

/**
 * Runs a full signature capture on the attached STU pad and resolves with
 * a base64 PNG data URL, or rejects with a user-facing message.
 *
 * Mirrors the capture()/GetHash() flow in Wacom's SigCaptX-Capture.html
 * sample: a small MD5 hash of the signer's name is bound into the
 * signature object before capture (tamper-evidence), then RenderBitmap is
 * called with the base64 output flag.
 *
 * Forces a fresh session right before capturing (rather than reusing
 * whatever was booted when the page/widget first loaded) - SigCaptX runs
 * a per-user background session that may go stale if idle time passes
 * between session-start and the actual capture click, which can let
 * Capture() nominally succeed while the follow-up RenderBitmap() fails.
 *
 * @param {Object} params
 * @param {string} params.who - name of the signer, shown on the pad/dialog
 * @param {string} params.why - reason for signing, shown on the pad/dialog
 * @returns {Promise<string>} data:image/png;base64,... string
 */
export async function captureWacomSignature({ who, why }) {
    sessionReadyPromise = null; // always start this capture with a fresh session
    const ready = await checkWacomBridge();
    if (!ready) {
        throw new Error(
            "Wacom SigCaptX service was not detected on this PC. Make sure the " +
            "Wacom Signature SDK + SigCaptX Library are installed and running " +
            "(and that you've accepted the local HTTPS certificate once - see " +
            "this module's README)."
        );
    }

    const sdk = window.wgssSignatureSDK;
    const dynCapt = window.dynCapt;
    const sigCtl = window.sigCtl;
    const whoText = who || "";
    const whyText = why || "Document Approval";

    return new Promise((resolve, reject) => {
        const hash = new sdk.Hash(onHashConstructor);

        function onHashConstructor(hashV, status) {
            if (status !== sdk.ResponseStatus.OK) {
                reject(new Error(`Wacom hash init failed (status ${status}).`));
                return;
            }
            hash.Clear(onClear);
        }

        function onClear(hashV, status) {
            if (status !== sdk.ResponseStatus.OK) {
                reject(new Error(`Wacom hash clear failed (status ${status}).`));
                return;
            }
            hash.PutType(sdk.HashType.HashMD5, onPutType);
        }

        function onPutType(hashV, status) {
            if (status !== sdk.ResponseStatus.OK) {
                reject(new Error(`Wacom hash type failed (status ${status}).`));
                return;
            }
            const variant = new sdk.Variant();
            variant.Set(whoText);
            hash.Add(variant, onAdd);
        }

        function onAdd(hashV, status) {
            if (status !== sdk.ResponseStatus.OK) {
                reject(new Error(`Wacom hash add failed (status ${status}).`));
                return;
            }
            dynCapt.Capture(sigCtl, whoText, whyText, hash, null, onDynCaptCapture);
        }

        function onDynCaptCapture(dynCaptV, sigObjV, status) {
            switch (status) {
                case sdk.DynamicCaptureResult.DynCaptOK: {
                    // Confirmed via Wacom's own untouched sample that 32BPP
                    // color rendering works fine on this pad - match it
                    // exactly, including reading real width/height from
                    // imageBox rather than a guessed fixed size.
                    const outputFlags = sdk.RBFlags.RenderOutputBase64 | sdk.RBFlags.RenderColor32BPP;
                    const imageBox = document.getElementById("imageBox");
                    // NOTE: these are declared with `const` in
                    // SigCaptX-Globals.js, so unlike the SDK objects above
                    // (declared with `var`) they do NOT attach to `window`.
                    // Bare identifiers resolve correctly here because this
                    // module executes in the same realm as that classic
                    // script's top-level scope - `window.BITMAP_*` would
                    // silently be undefined.
                    sigObjV.RenderBitmap(
                        BITMAP_IMAGEFORMAT,
                        imageBox.clientWidth, imageBox.clientHeight,
                        BITMAP_INKWIDTH,
                        BITMAP_INKCOLOR,
                        BITMAP_BACKGROUNDCOLOR,
                        outputFlags,
                        BITMAP_PADDING_X,
                        BITMAP_PADDING_Y,
                        onRenderBitmap
                    );
                    break;
                }
                case sdk.DynamicCaptureResult.DynCaptCancel:
                    reject(new Error("Signature capture was cancelled."));
                    break;
                case sdk.DynamicCaptureResult.DynCaptPadError:
                    reject(new Error("No Wacom pad capture service available. Check the pad is plugged in."));
                    break;
                case sdk.DynamicCaptureResult.DynCaptNotLicensed:
                    reject(new Error("No valid Wacom Signature license found."));
                    break;
                default:
                    reject(new Error(`Wacom pad capture failed (status ${status}).`));
            }
        }

        function onRenderBitmap(sigObjV, imageBase64, status) {
            if (status !== sdk.ResponseStatus.OK) {
                reject(new Error(`Wacom RenderBitmap failed (status ${status}).`));
                return;
            }
            resolve(`data:image/png;base64,${imageBase64}`);
        }
    });
}
