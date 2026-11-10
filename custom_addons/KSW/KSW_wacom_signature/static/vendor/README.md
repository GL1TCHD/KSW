# Wacom SigCaptX vendor files

These 5 files are Wacom's own MIT-licensed sample code from
https://github.com/Wacom-Developer/sdk-for-signature-sigcaptx-windows
(the `Samples/` folder), copied in verbatim:

- `base64.js`
- `SigCaptX-Globals.js`
- `SigCaptX-Utils.js`
- `SigCaptX-SessionControl.js`
- `wgssSigCaptX.js`

The only change made to any of them: `LICENCEKEY` in `SigCaptX-Globals.js`
was filled in with Wacom's public, free-of-charge "Lite" license (published
in that same repo's `GETTING-STARTED.md`). It covers standard signature
capture; it does **not** cover signature encryption or ISO signature
formatting. If you need those, get your own license from
https://developer.wacom.com/developer-dashboard and replace the
`LICENCEKEY` constant.

`static/src/js/sigcaptx_bridge.js` loads these in the same order Wacom's
own `SigCaptX-Capture.html` sample does, then drives the same
`actionWhenRestarted` / `dynCapt.Capture` / `sigObj.RenderBitmap` calls the
sample uses - just wrapped in a Promise instead of writing into page DOM
elements. If none of this is detected (files missing, or the native
SigCaptX service not installed/running on the client PC), the module
falls back to the built-in draw-with-mouse/touch signature pad - no error
shown to the end user.

See `../../README.md` for the client-PC install checklist (Wacom
Signature SDK + SigCaptX Library, and a one-time HTTPS certificate
acceptance).
