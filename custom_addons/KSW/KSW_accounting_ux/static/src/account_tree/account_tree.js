/** @odoo-module **/
import { Component, onWillStart, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { formatFloat } from "@web/core/utils/numbers";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const UNASSIGNED = "u";

/**
 * Chart of Accounts as a tree: groups (BAS levels 1-4) open one level at a
 * time down to the leaf accounts, the way BAS shows it. The whole chart comes
 * in one call; expanding, searching and the group balances are all local.
 */
export class KswAccountTree extends Component {
    static template = "KSW_accounting_ux.AccountTree";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.searchRef = useRef("search");
        this.state = useState({
            loading: true,
            query: "",
            expanded: {},
            selected: null,
            showBalance: true,
        });
        this.labels = {
            title: _t("Chart of Accounts"),
            search: _t("Search by code or name…"),
            level: _t("Level"),
            all: _t("All"),
            collapse: _t("Collapse all"),
            balance: _t("Balance"),
            code: _t("Code"),
            name: _t("Account"),
            type: _t("Type"),
            group: _t("group"),
            groups: _t("groups"),
            account: _t("account"),
            accounts: _t("accounts"),
            unassigned: _t("Accounts outside any group"),
            journalItems: _t("Journal Items"),
            openAccount: _t("Open account"),
            noMatch: _t("No account or group matches"),
            listView: _t("List view"),
            expandTo: _t("Expand to"),
            matches: _t("matches"),
        };
        onWillStart(() => this.load());
    }

    async load() {
        const data = await this.orm.call("account.account", "ksw_get_account_tree", []);
        this.data = data;
        this.state.showBalance = data.show_balance;
        this.build(data);
        this.state.loading = false;
    }

    // ------------------------------------------------------------------
    // Tree
    // ------------------------------------------------------------------

    build(data) {
        const nodes = {};
        for (const g of data.groups) {
            nodes[`g${g.id}`] = {
                key: `g${g.id}`, id: g.id, kind: "group", code: g.code, name: g.name,
                parentKey: g.parent_id ? `g${g.parent_id}` : null,
                children: [], balance: 0, nAccounts: 0, nGroups: 0,
            };
        }
        const unassigned = {
            key: UNASSIGNED, id: null, kind: "group", code: "", name: this.labels.unassigned,
            parentKey: null, children: [], balance: 0, nAccounts: 0, nGroups: 0, virtual: true,
        };
        for (const a of data.accounts) {
            const parentKey = a.group_id && nodes[`g${a.group_id}`] ? `g${a.group_id}` : UNASSIGNED;
            nodes[`a${a.id}`] = {
                key: `a${a.id}`, id: a.id, kind: "account", code: a.code, name: a.name,
                type: a.type, reconcile: a.reconcile, balance: a.balance, parentKey,
            };
        }
        if (Object.values(nodes).some((n) => n.parentKey === UNASSIGNED)) {
            nodes[UNASSIGNED] = unassigned;
        }
        const roots = [];
        for (const node of Object.values(nodes)) {
            const parent = node.parentKey && nodes[node.parentKey];
            if (parent) {
                parent.children.push(node);
            } else {
                node.parentKey = null;
                roots.push(node);
            }
        }
        const byCode = (a, b) =>
            (a.virtual ? 1 : 0) - (b.virtual ? 1 : 0) ||
            a.code.localeCompare(b.code, undefined, { numeric: false }) ||
            String(a.name).localeCompare(String(b.name));
        const finish = (node, depth) => {
            node.depth = depth;
            node.guides = Array.from({ length: depth }, (_, i) => i);
            node.search = `${node.code} ${node.name}`.toLowerCase();
            if (node.kind !== "group") {
                return;
            }
            node.children.sort(byCode);
            for (const child of node.children) {
                finish(child, depth + 1);
                node.balance += child.balance;
                if (child.kind === "group") {
                    node.nGroups += 1;
                    node.nAccounts += child.nAccounts;
                } else {
                    node.nAccounts += 1;
                }
            }
        };
        roots.sort(byCode);
        roots.forEach((root) => finish(root, 0));
        this.nodes = nodes;
        this.roots = roots;
        this.totals = {
            groups: data.groups.length,
            accounts: data.accounts.length,
            levels: 1 + Math.max(0, ...Object.values(nodes).filter((n) => n.kind === "group").map((n) => n.depth)),
        };
    }

    /** Keys of the nodes that match the query, plus all their ancestors. */
    get matchInfo() {
        const query = this.state.query.trim().toLowerCase();
        if (query === this._lastQuery) {
            return this._lastMatch;
        }
        let info = null;
        if (query) {
            const matched = new Set();
            const keep = new Set();
            for (const node of Object.values(this.nodes)) {
                if (node.search.includes(query)) {
                    matched.add(node.key);
                    for (let n = node; n; n = n.parentKey && this.nodes[n.parentKey]) {
                        keep.add(n.key);
                    }
                }
            }
            info = { matched, keep, query };
        }
        this._lastQuery = query;
        this._lastMatch = info;
        return info;
    }

    isExpanded(node) {
        const toggled = this.state.expanded[node.key];
        if (toggled !== undefined) {
            return toggled;
        }
        // While searching, the path to every match opens by itself.
        const match = this.matchInfo;
        return Boolean(match && match.keep.has(node.key) && !match.matched.has(node.key));
    }

    /** The rows currently on screen, depth-first. */
    get rows() {
        const match = this.matchInfo;
        const rows = [];
        const walk = (nodes, filtered) => {
            for (const node of nodes) {
                if (filtered && !match.keep.has(node.key)) {
                    continue;
                }
                rows.push(node);
                if (node.kind === "group" && this.isExpanded(node)) {
                    // Inside a matching group show everything, not just the hits.
                    walk(node.children, filtered && !match.matched.has(node.key));
                }
            }
        };
        walk(this.roots, Boolean(match));
        return rows;
    }

    get selectedPath() {
        const path = [];
        for (let n = this.nodes?.[this.state.selected]; n; n = n.parentKey && this.nodes[n.parentKey]) {
            path.unshift(n);
        }
        return path;
    }

    // ------------------------------------------------------------------
    // Actions
    // ------------------------------------------------------------------

    onRowClick(node) {
        this.state.selected = node.key;
        if (node.kind === "group") {
            this.toggle(node);
        }
    }

    toggle(node) {
        this.state.expanded[node.key] = !this.isExpanded(node);
    }

    /** Show groups down to ``level`` (1 = the roots only), collapsed there. */
    expandToLevel(level) {
        const expanded = {};
        for (const node of Object.values(this.nodes)) {
            if (node.kind === "group") {
                expanded[node.key] = node.depth < level - 1;
            }
        }
        this.state.expanded = expanded;
    }

    expandAll() {
        this.expandToLevel(Infinity);
    }

    collapseAll() {
        this.expandToLevel(1);
    }

    onSearchInput(ev) {
        this.state.query = ev.target.value;
        // A new search starts from its own auto-opened paths.
        this.state.expanded = {};
    }

    clearSearch() {
        this.state.query = "";
        this.state.expanded = {};
        this.searchRef.el?.focus();
    }

    leafIds(node) {
        if (node.kind === "account") {
            return [node.id];
        }
        return node.children.flatMap((child) => this.leafIds(child));
    }

    openJournalItems(node) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: `${node.code} ${node.name}`.trim(),
            res_model: "account.move.line",
            views: [[false, "list"], [false, "pivot"], [false, "form"]],
            domain: [["account_id", "in", this.leafIds(node)], ["parent_state", "=", "posted"]],
            context: { search_default_group_by_account: node.kind === "group" ? 1 : 0 },
        });
    }

    openAccount(node) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "account.account",
            res_id: node.id,
            views: [[false, "form"]],
        });
    }

    openList() {
        return this.action.doAction("account.action_account_form");
    }

    // ------------------------------------------------------------------
    // Display
    // ------------------------------------------------------------------

    amount(value) {
        if (!value || Math.abs(value) < 0.005) {
            return "–";
        }
        return formatFloat(value, { digits: [16, this.data.currency.digits] });
    }

    /** The matched part of ``text`` split out, for highlighting. */
    highlight(text) {
        const match = this.matchInfo;
        text = String(text || "");
        if (!match) {
            return [{ text, hit: false }];
        }
        const at = text.toLowerCase().indexOf(match.query);
        if (at < 0) {
            return [{ text, hit: false }];
        }
        return [
            { text: text.slice(0, at), hit: false },
            { text: text.slice(at, at + match.query.length), hit: true },
            { text: text.slice(at + match.query.length), hit: false },
        ];
    }

    levelClass(node) {
        return node.kind === "group" ? `o_ksw_tree_l${Math.min(node.depth + 1, 5)}` : "o_ksw_tree_leaf";
    }

    get levels() {
        return Array.from({ length: this.totals.levels }, (_, i) => i + 1);
    }

    get matchCount() {
        return this.matchInfo ? this.matchInfo.matched.size : 0;
    }
}

registry.category("actions").add("ksw_account_tree", KswAccountTree);
