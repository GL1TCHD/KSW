import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { useSortable } from "@web/core/utils/sortable_owl";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

const { DateTime } = luxon;

const MAX_APP_RESULTS = 6;
const MAX_SCREEN_RESULTS = 10;

function norm(text) {
    // Case-insensitive, and alef/teh-marbuta/yeh variants match each other.
    return (text || "")
        .toLowerCase()
        .replace(/[أإآ]/g, "ا")
        .replace(/ة/g, "ه")
        .replace(/ى/g, "ي");
}

/**
 * KSW Home: what is waiting for me, then my apps, then what I opened last.
 *
 * Ordered by what users of SAP (My Home "To-Dos"), Dynamics (count tiles,
 * Recent) and Oracle (smart search) praise; built against what they complain
 * about: only non-zero counters show (no 70-tile launchpad), one server call
 * (slowness), visible search over every screen (sprawling menu trees), and
 * the apps are personal (hide / reorder) with a company default.
 */
export class KswHome extends Component {
    static template = "KSW_backend_theme.Home";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.menuService = useService("menu");
        this.recent = useService("ksw_recent");
        this.notification = useService("notification");
        this.state = useState({
            data: null,
            query: "",
            editing: false,
            draft: null,
            showHidden: false,
        });
        this.searchRef = useRef("search");
        this.appsRef = useRef("apps");
        this.screenIndex = null;

        onWillStart(async () => {
            this.state.data = await this.orm.call("res.users", "ksw_home_data", []);
        });
        onMounted(() => {
            document.body.classList.add("ksw_on_home");
            document.body.classList.remove("ksw_no_app");
            if (!this.env.isSmall) {
                this.searchRef.el?.focus();
            }
        });
        onWillUnmount(() => document.body.classList.remove("ksw_on_home"));

        useSortable({
            enable: () => this.state.editing,
            ref: this.appsRef,
            elements: ".ksw_home_app",
            cursor: "grabbing",
            onDrop: (params) => this.onAppDrop(params),
        });
    }

    // ---- header ---------------------------------------------------------

    get greeting() {
        const hour = new Date().getHours();
        if (hour < 12) {
            return _t("Good morning, %s", this.firstName);
        }
        if (hour < 18) {
            return _t("Good afternoon, %s", this.firstName);
        }
        return _t("Good evening, %s", this.firstName);
    }

    get firstName() {
        return (this.state.data?.user_name || user.name || "").split(" ")[0];
    }

    get today() {
        return DateTime.now().toLocaleString(DateTime.DATE_HUGE);
    }

    get waitingTotal() {
        const data = this.state.data;
        const counters = data.counters.reduce((sum, c) => sum + c.count, 0);
        return counters + data.activities.overdue + data.activities.today;
    }

    get summary() {
        const total = this.waitingTotal;
        // A label then the figure: Arabic noun agreement changes with the number.
        return total ? _t("Waiting for you: %s", total) : _t("Nothing is waiting for you.");
    }

    // ---- apps -----------------------------------------------------------

    get allApps() {
        return computeAppsAndMenuItems(this.menuService.getMenuAsTree("root")).apps;
    }

    get config() {
        return this.state.editing ? this.state.draft : this.state.data.config;
    }

    get orderedApps() {
        const rank = new Map(this.config.order.map((xmlid, i) => [xmlid, i]));
        return this.allApps
            .map((app, i) => ({ app, i }))
            .sort((a, b) => {
                const ra = rank.has(a.app.xmlid) ? rank.get(a.app.xmlid) : 1e6 + a.i;
                const rb = rank.has(b.app.xmlid) ? rank.get(b.app.xmlid) : 1e6 + b.i;
                return ra - rb;
            })
            .map(({ app }) => app);
    }

    get visibleApps() {
        const hidden = new Set(this.config.hidden);
        return this.orderedApps.filter((app) => this.state.editing || !hidden.has(app.xmlid));
    }

    get hiddenApps() {
        const hidden = new Set(this.config.hidden);
        return this.orderedApps.filter((app) => hidden.has(app.xmlid));
    }

    get hiddenToggleLabel() {
        return this.state.showHidden
            ? _t("Hide the hidden apps")
            : _t("Show hidden apps (%s)", this.hiddenApps.length);
    }

    toggleShowHidden() {
        this.state.showHidden = !this.state.showHidden;
    }

    isHidden(app) {
        return this.config.hidden.includes(app.xmlid);
    }

    openApp(app) {
        if (this.state.editing) {
            return;
        }
        this.menuService.selectMenu(this.menuService.getMenu(app.id));
    }

    startEdit() {
        this.state.draft = {
            order: this.orderedApps.map((app) => app.xmlid),
            hidden: [...this.state.data.config.hidden],
        };
        this.state.editing = true;
    }

    cancelEdit() {
        this.state.editing = false;
        this.state.draft = null;
    }

    toggleHidden(app) {
        const hidden = this.state.draft.hidden;
        const i = hidden.indexOf(app.xmlid);
        if (i >= 0) {
            hidden.splice(i, 1);
        } else {
            hidden.push(app.xmlid);
        }
    }

    onAppDrop({ element, previous }) {
        const order = this.state.draft.order.filter((x) => x !== element.dataset.xmlid);
        const at = previous ? order.indexOf(previous.dataset.xmlid) + 1 : 0;
        order.splice(at, 0, element.dataset.xmlid);
        this.state.draft.order = order;
    }

    async saveEdit() {
        const config = { ...this.state.draft };
        await this.orm.call("res.users", "ksw_home_save_config", [config]);
        Object.assign(this.state.data, { config, is_personal: true });
        this.cancelEdit();
    }

    async saveAsDefault() {
        const config = { ...this.state.draft };
        await this.orm.call("res.users", "ksw_home_save_default", [config]);
        this.notification.add(_t("Everyone without their own layout now sees this one."), {
            type: "success",
        });
    }

    async resetLayout() {
        const config = await this.orm.call("res.users", "ksw_home_reset_config", []);
        Object.assign(this.state.data, { config, is_personal: false });
        this.cancelEdit();
    }

    // ---- search ---------------------------------------------------------

    _buildScreenIndex() {
        const screens = [];
        const walk = (node, path, app) => {
            for (const child of node.childrenTree || []) {
                const childPath = [...path, child.name];
                if (child.childrenTree?.length) {
                    walk(child, childPath, app);
                } else if (child.actionID) {
                    screens.push({
                        menu: child,
                        app,
                        name: child.name,
                        path: path.join(" › "),
                        key: norm(childPath.join(" ")),
                    });
                }
            }
        };
        for (const app of this.menuService.getMenuAsTree("root").childrenTree) {
            walk(app, [app.name], app);
        }
        return screens;
    }

    get results() {
        const query = norm(this.state.query.trim());
        if (!query) {
            return null;
        }
        this.screenIndex ||= this._buildScreenIndex();
        const words = query.split(/\s+/);
        const hit = (key) => words.every((w) => key.includes(w));
        const apps = this.allApps.filter((app) => hit(norm(app.label))).slice(0, MAX_APP_RESULTS);
        const screens = this.screenIndex.filter((s) => hit(s.key)).slice(0, MAX_SCREEN_RESULTS);
        return { apps, screens, empty: !apps.length && !screens.length };
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.query = "";
        } else if (ev.key === "Enter") {
            const results = this.results;
            if (results?.apps.length) {
                this.openApp(results.apps[0]);
            } else if (results?.screens.length) {
                this.openScreen(results.screens[0]);
            }
        }
    }

    openScreen(screen) {
        this.menuService.selectMenu(screen.menu);
    }

    appIcon(appId) {
        const app = appId && this.menuService.getMenu(appId);
        return app?.webIconData || false;
    }

    // ---- waiting for me -------------------------------------------------

    /**
     * Open an action and put its app in the top bar. Without this the bar
     * keeps whatever app was used last (Inventory over a Time Off list).
     * No app (activities span every app): show no app's menus at all.
     */
    _openInApp(action, appId) {
        const app = appId && this.menuService.getMenu(appId);
        return this.actionService.doAction(action, {
            onActionReady: () => {
                if (app) {
                    this.menuService.setCurrentMenu(app);
                } else {
                    document.body.classList.add("ksw_no_app");
                }
            },
        });
    }

    openCounter(counter) {
        this._openInApp(counter.action, counter.app_id);
    }

    openActivities(when) {
        const today = DateTime.now().toISODate();
        const op = { overdue: "<", today: "=", planned: ">" }[when];
        this._openInApp({
            type: "ir.actions.act_window",
            name: _t("My Activities"),
            res_model: "mail.activity",
            domain: [
                ["user_id", "=", user.userId],
                ["date_deadline", op, today],
            ],
            views: [
                [false, "list"],
                [false, "form"],
            ],
        }, false);
    }

    configureCounters() {
        this.actionService.doAction("KSW_backend_theme.action_ksw_home_inbox_source");
    }

    // ---- recent ---------------------------------------------------------

    get recentItems() {
        return this.recent.list().slice(0, 8).map((entry) => ({
            ...entry,
            key: `${entry.model}_${entry.id}`,
            icon: this.appIcon(entry.appId),
            when: DateTime.fromMillis(entry.ts).toRelative(),
        }));
    }

    openRecent(entry) {
        this._openInApp({
            type: "ir.actions.act_window",
            res_model: entry.model,
            res_id: entry.id,
            views: [[false, "form"]],
        }, entry.appId);
    }
}

registry.category("actions").add("ksw_home", KswHome);
