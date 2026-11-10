import { markup } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";

// Per device, not per user: the same person may want dark on the phone at night
// and light on the office PC. "color_scheme" is the cookie the server reads to
// pick the stylesheet (ir.http.color_scheme); "ksw_color_mode" remembers the choice.
const MODE_COOKIE = "ksw_color_mode";
const SCHEME_COOKIE = "color_scheme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function systemPrefersDark() {
    return Boolean(browser.matchMedia && browser.matchMedia(DARK_QUERY).matches);
}

function schemeFor(mode) {
    if (mode === "auto") {
        return systemPrefersDark() ? "dark" : "light";
    }
    return mode === "dark" ? "dark" : "light";
}

export const kswAppearanceService = {
    start() {
        const getMode = () => cookie.get(MODE_COOKIE) || "light";

        const apply = (mode) => {
            cookie.set(MODE_COOKIE, mode);
            const scheme = schemeFor(mode);
            if (cookie.get(SCHEME_COOKIE) === scheme) {
                return;
            }
            cookie.set(SCHEME_COOKIE, scheme);
            // Reload only once the cookie really stuck: with cookies blocked this
            // would otherwise reload forever.
            if (cookie.get(SCHEME_COOKIE) === scheme) {
                browser.location.reload();
            }
        };

        if (getMode() === "auto") {
            apply("auto");
            browser.matchMedia?.(DARK_QUERY).addEventListener?.("change", () => {
                if (getMode() === "auto") {
                    apply("auto");
                }
            });
        }
        return { getMode, apply };
    },
};
registry.category("services").add("ksw_appearance", kswAppearanceService);

function appearanceItem(mode, icon, label, sequence) {
    return (env) => {
        const active = env.services.ksw_appearance.getMode() === mode;
        return {
            type: "item",
            id: `ksw_appearance_${mode}`,
            description: markup`
                <div class="d-flex align-items-center justify-content-between w-100 p-0">
                    <span><i class="fa ${icon} me-2" aria-hidden="true"></i>${label()}</span>
                    ${active ? markup`<i class="fa fa-check text-primary" aria-label="${_t("Selected")}"></i>` : ""}
                </div>`,
            callback: () => env.services.ksw_appearance.apply(mode),
            sequence,
        };
    };
}

const menu = registry.category("user_menuitems");
menu.add("ksw_appearance_separator", () => ({ type: "separator", sequence: 41 }));
menu.add("ksw_appearance_light", appearanceItem("light", "fa-sun-o", () => _t("Light"), 42));
menu.add("ksw_appearance_dark", appearanceItem("dark", "fa-moon-o", () => _t("Dark"), 43));
menu.add("ksw_appearance_auto", appearanceItem("auto", "fa-adjust", () => _t("Automatic (this device)"), 44));
