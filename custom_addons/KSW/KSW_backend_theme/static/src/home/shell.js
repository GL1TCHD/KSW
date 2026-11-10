import { Component } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { session } from "@web/session";
import { NavBar } from "@web/webclient/navbar/navbar";
import { WebClient } from "@web/webclient/webclient";

const HOME_ACTION = "KSW_backend_theme.action_ksw_home";
const RECENT_LIMIT = 12;

// Landing page: Home, unless the user has their own Home Action set
// (that path is taken earlier, in loadRouterState).
patch(WebClient.prototype, {
    _loadDefaultApp() {
        return this.env.services.action.doAction(HOME_ACTION, { clearBreadcrumbs: true });
    },
});

// The apps button opens Home: one app switcher (MuK's sidebar is hidden).
patch(NavBar.prototype, {
    kswOpenHome() {
        this.env.services.action.doAction(HOME_ACTION, { clearBreadcrumbs: true });
    },
});

/**
 * Recently opened records (Dynamics' "Recent"), kept in this browser only:
 * a per-viewer convenience, so losing it costs nothing.
 */
export const kswRecentService = {
    dependencies: ["action", "menu"],
    start(env, { action, menu }) {
        const key = `ksw_recent_${session.db}_${user.userId}`;
        const read = () => {
            try {
                return JSON.parse(browser.localStorage.getItem(key) || "[]");
            } catch {
                return [];
            }
        };
        const write = (list) => {
            try {
                browser.localStorage.setItem(key, JSON.stringify(list));
            } catch {
                // storage unavailable: Recent just stays empty
            }
        };
        const capture = () => {
            const controller = action.currentController;
            const props = controller?.props;
            if (controller?.view?.type !== "form" || !props?.resId || !props?.resModel) {
                return;
            }
            const entry = {
                model: props.resModel,
                id: props.resId,
                title: controller.displayName || controller.action?.name || "",
                appId: menu.getCurrentApp()?.id || null,
                ts: Date.now(),
            };
            const list = read().filter((e) => !(e.model === entry.model && e.id === entry.id));
            list.unshift(entry);
            write(list.slice(0, RECENT_LIMIT));
        };
        // The record's name arrives after the first render.
        env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", () => browser.setTimeout(capture, 700));
        return { list: read };
    },
};
registry.category("services").add("ksw_recent", kswRecentService);

// "No app" (set by Home for app-less screens such as My Activities) lasts until
// an app is chosen again.
export const kswNoAppService = {
    start(env) {
        env.bus.addEventListener("MENUS:APP-CHANGED", () =>
            document.body.classList.remove("ksw_no_app")
        );
    },
};
registry.category("services").add("ksw_no_app", kswNoAppService);

/** Visible search in the top bar: Odoo's Ctrl+K palette, which few users find. */
export class KswSearchButton extends Component {
    static template = "KSW_backend_theme.SearchButton";
    static props = {};

    setup() {
        this.command = useService("command");
    }

    open() {
        this.command.openMainPalette({ searchValue: "/" });
    }
}
registry.category("systray").add("ksw_search", { Component: KswSearchButton }, { sequence: 1000 });
