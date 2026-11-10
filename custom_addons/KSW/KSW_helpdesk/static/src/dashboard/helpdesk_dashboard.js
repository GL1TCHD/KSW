/** @odoo-module **/
import { Component, onMounted, onWillStart, onWillUnmount, onWillUpdateProps, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { formatFloat } from "@web/core/utils/numbers";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";

const PERIODS = [
    ["month", _t("This month")],
    ["last_month", _t("Last month")],
    ["quarter", _t("This quarter")],
    ["year", _t("This year")],
];

/** Grouped bar chart, Chart.js as Odoo ships it (same as the KSW overview pages). */
export class HelpdeskBarChart extends Component {
    static template = "KSW_helpdesk.BarChart";
    static props = { labels: Array, series: Array, height: { type: Number, optional: true } };

    setup() {
        this.canvas = useRef("canvas");
        onWillStart(() => loadBundle("web.chartjs_lib"));
        onMounted(() => this.draw());
        onWillUpdateProps((next) => this.draw(next));
        onWillUnmount(() => this.chart?.destroy());
    }

    draw(props = this.props) {
        this.chart?.destroy();
        if (!this.canvas.el) {
            return;
        }
        this.chart = new window.Chart(this.canvas.el, {
            type: "bar",
            data: {
                labels: props.labels,
                datasets: props.series.map((s) => ({
                    label: s.label, data: s.data, backgroundColor: s.color, borderRadius: 3, maxBarThickness: 22,
                })),
            },
            options: {
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom", labels: { boxWidth: 12 } } },
                scales: { x: { grid: { display: false } }, y: { ticks: { precision: 0 } } },
            },
        });
    }
}

export class HelpdeskDashboard extends Component {
    static template = "KSW_helpdesk.Dashboard";
    static components = { HelpdeskBarChart };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.periods = PERIODS;
        this.labels = {
            open: _t("Open tickets"), unassigned: _t("Unassigned open tickets"), overdue: _t("Overdue tickets"),
            blocked: _t("Blocked tickets"), urgent: _t("High and urgent open tickets"),
            received: _t("Received"), closed: _t("Closed"), rated: _t("Rated tickets"),
            openByAge: _t("Open tickets by age"), warranty: _t("Warranty expiring soon"),
            lowStock: _t("Low stock consumables"),
        };
        this.trendTitles = {
            day: _t("Received and closed, by day"), week: _t("Received and closed, by week"),
            month: _t("Received and closed, by month"),
        };
        this.state = useState({ period: "month", data: null, loading: true });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("helpdesk.dashboard", "get_dashboard_data", [this.state.period]);
        this.state.loading = false;
    }

    setPeriod(period) {
        this.state.period = period;
        this.load();
    }

    n(value) {
        return formatFloat(value || 0, { digits: [16, 0] });
    }

    /** Resolution time: hours under two days, days above. */
    duration(hours) {
        if (hours < 48) {
            return `${formatFloat(hours, { digits: [16, 1] })} ${_t("h")}`;
        }
        return `${formatFloat(hours / 24, { digits: [16, 1] })} ${_t("days")}`;
    }

    change(current, previous) {
        if (!previous) {
            return "";
        }
        const pct = ((current - previous) / Math.abs(previous)) * 100;
        return `${pct >= 0 ? "▲" : "▼"} ${Math.abs(pct).toFixed(0)}%`;
    }

    width(value, rows) {
        const max = Math.max(...rows.map((r) => Math.abs(r.count)), 1);
        return `${Math.round((Math.abs(value) / max) * 100)}%`;
    }

    open(name, domain, model = "helpdesk.ticket") {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: model,
            domain,
            views: [[false, "list"], [false, "kanban"], [false, "form"]],
            context: { create: false },
            target: "current",
        });
    }

    openStandard() {
        return this.action.doAction("KSW_helpdesk.action_helpdesk_ticket_all");
    }

    get trendTitle() {
        return this.trendTitles[this.state.data.trend_unit];
    }

    get trendLabels() {
        return this.state.data.trend.map((t) => t.label);
    }

    get trendSeries() {
        const t = this.state.data.trend;
        return [
            { label: this.labels.received, data: t.map((r) => r.received), color: "#3b82f6" },
            { label: this.labels.closed, data: t.map((r) => r.closed), color: "#10b981" },
        ];
    }
}

registry.category("actions").add("ksw_helpdesk_dashboard", HelpdeskDashboard);
