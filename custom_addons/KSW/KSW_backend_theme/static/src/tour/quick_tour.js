import { Component, onMounted, onWillUnmount, reactive, useRef, useState } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { session } from "@web/session";

/**
 * Four-step quick tour of the new interface, shown once per account.
 *
 * Not built on web_tour: its automatic tours only run for users with the
 * "Onboarding" flag (off for everyone here), and switching that on would also
 * start Odoo's own onboarding tours. This one spotlights the real control,
 * explains it in one sentence, and is marked done (res.users.ksw_tour_done)
 * when finished or skipped. The user menu can replay it.
 */
const STEPS = [
    {
        target: (small) => (small ? ".o_menu_toggle" : ".ksw_home_button"),
        title: () => _t("Home"),
        body: (small) =>
            small
                ? _t("Open this menu and press Home to return to the Home page at any time: what is waiting for you, all your apps, and what you opened recently.")
                : _t("Press this button at any time to return to Home: what is waiting for you, all your apps, and what you opened recently. Shortcut: Alt+H."),
    },
    {
        target: () => ".ksw_home_search",
        title: () => _t("Search"),
        body: () => _t("Type the name of any app or screen and press Enter to open it. From any other page, use the search button at the top or Ctrl+K."),
    },
    {
        target: () => ".ksw_home_waiting_section",
        title: () => _t("Waiting for you"),
        body: () => _t("Approvals and activities that need you appear here as cards with a count. Press a card to open exactly those records. When nothing is waiting, it tells you so."),
    },
    {
        target: () => ".ksw_home_customise",
        title: () => _t("Hide the apps you don't use"),
        body: () => _t("Press Customise, then the eye on an app to hide it, or drag the apps into your own order, and press Save. Hidden apps stay one click away under Show hidden apps."),
    },
    {
        target: () => ".o_user_menu",
        title: () => _t("Appearance"),
        body: () => _t("Open your profile menu and choose Light (the original look), Dark, or Automatic to follow this device."),
    },
    {
        target: () => ".o_user_menu",
        title: () => _t("Text size"),
        body: () => _t("In the same menu, open My Preferences and choose a text size: Automatic, Standard or Large. You can replay this tour from that menu too: Quick tour."),
    },
];

const HOME_ACTION = "KSW_backend_theme.action_ksw_home";

export const kswQuickTourService = {
    dependencies: ["orm", "action"],
    start(env, { orm, action }) {
        const state = reactive({ active: false, index: 0 });
        const finish = async () => {
            state.active = false;
            if (!session.ksw_tour_done) {
                session.ksw_tour_done = true;
                await orm.call("res.users", "ksw_tour_mark_done", []);
            }
        };
        // Most steps point at Home, so the tour always runs there.
        const start = async () => {
            if (!document.querySelector(".ksw_home")) {
                await action.doAction(HOME_ACTION, { clearBreadcrumbs: true });
            }
            state.index = 0;
            state.active = true;
        };
        // Once per account: only internal users get the flag in the session.
        if (session.ksw_tour_done === false) {
            browser.setTimeout(start, 1500);
        }
        return { state, start, finish, steps: STEPS };
    },
};
registry.category("services").add("ksw_quick_tour", kswQuickTourService);

const PAD = 6;
const CARD_W = 340;

export class KswQuickTour extends Component {
    static template = "KSW_backend_theme.QuickTour";
    static props = {};

    setup() {
        this.tour = useService("ksw_quick_tour");
        this.state = useState(this.tour.state);
        this.pos = useState({ spot: null, card: null });
        this.nextRef = useRef("next");
        const reposition = () => this.place();
        this.onKey = (ev) => {
            if (this.state.active && ev.key === "Escape") {
                this.tour.finish();
            }
        };
        onMounted(() => {
            browser.addEventListener("resize", reposition);
            browser.addEventListener("keydown", this.onKey);
            this.interval = browser.setInterval(reposition, 400);
        });
        onWillUnmount(() => {
            browser.removeEventListener("resize", reposition);
            browser.removeEventListener("keydown", this.onKey);
            browser.clearInterval(this.interval);
        });
    }

    get small() {
        return this.env.isSmall;
    }

    get step() {
        return this.tour.steps[this.state.index];
    }

    get counter() {
        return _t("%(current)s of %(total)s", {
            current: this.state.index + 1,
            total: this.tour.steps.length,
        });
    }

    get isLast() {
        return this.state.index === this.tour.steps.length - 1;
    }

    place() {
        if (!this.state.active) {
            return;
        }
        const el = document.querySelector(this.step.target(this.small));
        const vw = browser.innerWidth;
        const vh = browser.innerHeight;
        const width = Math.min(CARD_W, vw - 24);
        if (!el || !el.offsetParent) {
            // Target not on this screen: a centred card still explains it.
            Object.assign(this.pos, {
                spot: null,
                card: `width:${width}px; top:${Math.round(vh / 3)}px; left:${Math.round((vw - width) / 2)}px;`,
            });
            return;
        }
        if (this.scrolledFor !== this.state.index) {
            this.scrolledFor = this.state.index;
            el.scrollIntoView({ block: "nearest" });
        }
        const r = el.getBoundingClientRect();
        const spot = `top:${r.top - PAD}px; left:${r.left - PAD}px; width:${r.width + PAD * 2}px; height:${r.height + PAD * 2}px;`;
        // Below the target if it fits, else above, else pinned to the bottom
        // (a tall target such as the Waiting for you section).
        const cardH = 230;
        const below = r.bottom + PAD + 14;
        const above = r.top - PAD - 14 - cardH;
        const top = below + cardH < vh ? below : above >= 12 ? above : vh - cardH - 12;
        const left = Math.min(Math.max(12, r.left + r.width / 2 - width / 2), vw - width - 12);
        Object.assign(this.pos, { spot, card: `width:${width}px; top:${top}px; left:${left}px;` });
    }

    next() {
        if (this.isLast) {
            this.tour.finish();
        } else {
            this.state.index++;
            this.place();
        }
    }

    back() {
        if (this.state.index > 0) {
            this.state.index--;
            this.place();
        }
    }

    skip() {
        this.tour.finish();
    }
}
registry.category("main_components").add("KswQuickTour", { Component: KswQuickTour });

registry.category("user_menuitems").add("ksw_quick_tour", (env) => ({
    type: "item",
    id: "ksw_quick_tour",
    description: _t("Quick tour"),
    callback: () => env.services.ksw_quick_tour.start(),
    sequence: 45,
}));
