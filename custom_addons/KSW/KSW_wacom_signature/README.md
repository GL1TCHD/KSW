# KSW Wacom Signature

Upload a PDF, capture a handwritten signature, and stamp it onto a chosen
page/position of the document.

## Two capture paths

1. **Draw manually** (works today, zero setup) - Odoo's own signature pad
   (`signature_pad` library, mouse/touch/pointer events). This also works
   fine with a Wacom pad that is only plugged in as a generic HID pointer
   device - the browser sees pen position (and pressure, on supported
   pads) exactly like a mouse.

2. **Capture from Wacom STU pad via SigCaptX** - real biometric capture
   from the pad. The vendor JS bridge is already included in
   `static/vendor/` (Wacom's own MIT-licensed sample code, with their
   public free "Lite" license baked in - see `static/vendor/README.md`).
   What's still required is **native software on the Windows PC that has
   the pad plugged in** - this cannot be installed from this repo, it has
   to be done on that machine directly.

The "Capture from Wacom Pad" button on the Signature Document form
automatically detects whether path 2 is available and falls back to path 1
if not, so the module is fully usable before any Wacom-specific setup is
done.

## Setting up real STU pad capture (on the Windows PC with the pad)

**Important: this must be the PC actually running the browser tab** - not
this Odoo server. If you access Odoo via Remote Desktop into a separate
Windows PC, it's that RDP-target PC that needs this, with the pad's USB
plugged into it (and USB device redirection enabled in your RDP client if
it isn't a physical local connection).

1. Go to https://developer.wacom.com/developer-dashboard, log in / register
   with a Wacom ID, navigate to **Downloads for signature**, and download
   the **Wacom Ink SDK for signature (Windows)** package. It contains two
   installers: `SignatureSDK` and `SigCaptX`.
2. Install both (a combined `Wacom-SigCaptX-XX.exe` installer is usually
   provided - default options install to
   `C:\Program Files (x86)\Common Files\WacomGSS`). Run this **after**
   installing/running whichever browser you'll use, so the installer can
   detect it.
3. SigCaptX only accepts **HTTPS** connections by default, using a
   self-signed certificate on `localhost`. The very first time, open
   `https://127.0.0.1:8000` (or check the SigCaptX registry key
   `HKEY_LOCAL_MACHINE\SOFTWARE\Wacom\SigCaptX` for the actual port range)
   directly in the same browser and accept the security warning once -
   without this step the browser silently can't reach the local service
   and the button will keep saying "not detected."
4. Plug in the Wacom STU pad, open the Signature Document form in Odoo
   from that same PC/browser, go to the Signature tab. The button should
   now read "Capture from Wacom Pad."

No Odoo-side configuration is needed - the license is already embedded in
`static/vendor/SigCaptX-Globals.js` (see that folder's README for details,
and how to swap in your own license if you need the functions the free
Lite license excludes).

## How the PDF gets signed

`ksw.signature.document._stamp_signature_on_pdf()` overlays the captured
PNG onto one page of the source PDF using `odoo.tools.pdf` +
`reportlab.pdfgen.canvas`, following the same reader/writer/mergePage
pattern Odoo core uses for `add_banner()`. Position (`pos_x`/`pos_y`, in
points from the bottom-left of the page) and size (`sig_width`/
`sig_height`) are plain fields on the record - open the Source Document
tab to check where things land on the page before signing.
