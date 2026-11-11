import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { SignatureDialog } from "@web/core/signature/signature_dialog";
import { useService } from "@web/core/utils/hooks";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { Component, useState } from "@odoo/owl";

import { captureWacomSignature, checkWacomBridge, isWacomBridgeAvailable } from "./sigcaptx_bridge";

/**
 * Button widget: "Capture from Wacom Pad".
 *
 * If the SigCaptX bridge (see sigcaptx_bridge.js) is reachable, captures
 * directly from the attached STU pad and writes the result plus
 * `captureMethodField = 'wacom_pad'` onto the record.
 *
 * Otherwise falls back to Odoo's own draw/type/upload signature dialog and
 * writes `captureMethodField = 'manual'` - this also covers a Wacom tablet
 * used as a plain HID pointer device, since the browser draws whatever the
 * pen reports through standard pointer events.
 */
export class KswWacomCaptureWidget extends Component {
    static template = "KSW_wacom_signature.KswWacomCaptureWidget";
    static props = {
        ...standardWidgetProps,
        signatureField: { type: String },
        captureMethodField: { type: String, optional: true },
        sigTextField: { type: String, optional: true },
        bindHashField: { type: String, optional: true },
        fullName: { type: String, optional: true },
        string: { type: String, optional: true },
    };

    setup() {
        this.dialogService = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({ checking: true, bridgeAvailable: false });
        checkWacomBridge().then((available) => {
            this.state.checking = false;
            this.state.bridgeAvailable = available;
        });
    }

    get defaultName() {
        const { fullName, record } = this.props;
        if (!fullName || !record.data[fullName]) {
            return "";
        }
        const value = record.data[fullName];
        return record.fields[fullName].type === "many2one" ? value.display_name : value;
    }

    async onClickCapture() {
        if (this.state.checking) {
            return;
        }
        if (isWacomBridgeAvailable()) {
            await this._captureFromPad();
        } else {
            this._captureManually();
        }
    }

    async _captureFromPad() {
        try {
            const { bindHashField, record } = this.props;
            const { dataUrl, sigText } = await captureWacomSignature({
                who: this.defaultName,
                why: _t("Document Approval"),
                bindData: (bindHashField && record.data[bindHashField]) || "",
            });
            await this._saveSignature(dataUrl, "wacom_pad", sigText);
        } catch (error) {
            this.notification.add(error.message || _t("Wacom pad capture failed."), {
                type: "danger",
            });
        }
    }

    _captureManually() {
        const dialogProps = {
            defaultName: this.defaultName,
            nameAndSignatureProps: {
                mode: "draw",
                displaySignatureRatio: 3,
                signatureType: "signature",
                noInputName: true,
            },
            uploadSignature: ({ signatureImage }) => this._saveSignature(signatureImage, "manual"),
        };
        this.dialogService.add(SignatureDialog, dialogProps);
    }

    async _saveSignature(dataUrl, method, sigText = false) {
        const values = {
            [this.props.signatureField]: dataUrl.split(",")[1] || false,
        };
        if (this.props.captureMethodField) {
            values[this.props.captureMethodField] = method;
        }
        if (this.props.sigTextField) {
            values[this.props.sigTextField] = sigText || false;
        }
        await this.props.record.update(values);
    }
}

export const kswWacomCaptureWidget = {
    component: KswWacomCaptureWidget,
    extractProps: ({ attrs }) => ({
        signatureField: attrs.signature_field || "signature_image",
        captureMethodField: attrs.capture_method_field,
        sigTextField: attrs.sigtext_field,
        bindHashField: attrs.bind_hash_field,
        fullName: attrs.full_name,
        string: attrs.string || _t("Capture from Wacom Pad"),
    }),
};

registry.category("view_widgets").add("ksw_wacom_capture_signature", kswWacomCaptureWidget);
