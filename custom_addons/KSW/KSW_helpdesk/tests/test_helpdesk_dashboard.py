from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestHelpdeskDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        agent_group = cls.env.ref('KSW_helpdesk.group_helpdesk_agent')
        cls.agent = cls.env['res.users'].create({
            'name': 'HD Dash Agent', 'login': 'hd_dash_agent',
            'group_ids': [(6, 0, [agent_group.id])],
        })
        cls.employee_user = cls.env['res.users'].create({
            'name': 'HD Dash Employee', 'login': 'hd_dash_employee',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.employee = cls.env['hr.employee'].create({'name': 'HD Dash Caller'})
        cls.category = cls.env['helpdesk.ticket.category'].search([('ticket_type', '=', 'incident')], limit=1)
        Ticket = cls.env['helpdesk.ticket']
        cls.open_ticket = Ticket.create({
            'name': 'Printer jam', 'description': '<p>x</p>', 'employee_id': cls.employee.id,
            'category_id': cls.category.id, 'priority': '3',
            'deadline': fields.Date.today() - timedelta(days=1),
        })
        cls.closed_ticket = Ticket.create({
            'name': 'Password reset', 'description': '<p>x</p>', 'employee_id': cls.employee.id,
            'category_id': cls.category.id, 'user_id': cls.agent.id,
            'resolution_notes': '<p>Reset it.</p>',
        })
        cls.closed_ticket.with_user(cls.agent).action_close()
        cls.closed_ticket.satisfaction = 'great'

    def test_figures_include_the_tickets(self):
        data = self.env['helpdesk.dashboard'].with_user(self.agent).get_dashboard_data('month')
        Ticket = self.env['helpdesk.ticket']
        self.assertIn(self.open_ticket, Ticket.search(data['open']['domain']))
        self.assertIn(self.open_ticket, Ticket.search(data['overdue']['domain']))
        self.assertIn(self.open_ticket, Ticket.search(data['urgent']['domain']))
        self.assertIn(self.open_ticket, Ticket.search(data['open']['unassigned_domain']))
        self.assertNotIn(self.closed_ticket, Ticket.search(data['open']['domain']))
        self.assertIn(self.closed_ticket, Ticket.search(data['closed']['domain']))
        self.assertGreaterEqual(data['satisfaction']['rated'], 1)
        agent_row = next(r for r in data['agents'] if r['name'] == self.agent.name)
        self.assertEqual(agent_row['closed'], 1)
        self.assertEqual(Ticket.search(agent_row['domain']), self.closed_ticket)
        for period in ('last_month', 'quarter', 'year'):
            self.env['helpdesk.dashboard'].with_user(self.agent).get_dashboard_data(period)

    def test_employee_is_refused(self):
        with self.assertRaises(AccessError):
            self.env['helpdesk.dashboard'].with_user(self.employee_user).get_dashboard_data()

    def test_overview_menu_is_it_team_only(self):
        menu = self.env.ref('KSW_helpdesk.menu_helpdesk_overview')
        Menu = self.env['ir.ui.menu']
        self.assertIn(menu.id, Menu.with_user(self.agent)._visible_menu_ids())
        self.assertNotIn(menu.id, Menu.with_user(self.employee_user)._visible_menu_ids())


@tagged('post_install', '-at_install')
class TestHelpdeskDashboardUI(HttpCase):
    """The IT Team's Helpdesk app opens on the overview; an employee's opens on My Tickets."""

    def test_landing_pages(self):
        agent_group = self.env.ref('KSW_helpdesk.group_helpdesk_agent')
        self.env['res.users'].create({
            'name': 'hd_ui_agent', 'login': 'hd_ui_agent', 'password': 'hd_ui_agent',
            'group_ids': [(6, 0, [agent_group.id])]})
        self.env['res.users'].create({
            'name': 'hd_ui_employee', 'login': 'hd_ui_employee', 'password': 'hd_ui_employee',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])]})
        root = self.env.ref('KSW_helpdesk.menu_helpdesk_root')
        self.browser_js(
            f'/odoo?menu_id={root.id}', "console.log('test successful')",
            ready="document.querySelectorAll('.o_hd_dashboard .o_hd_chart canvas').length > 0",
            login='hd_ui_agent', timeout=120)
        self.browser_js(
            f'/odoo?menu_id={root.id}', "console.log('test successful')",
            ready="document.querySelector('.o_breadcrumb')?.textContent.includes('My Tickets')"
                  " && !document.querySelector('.o_hd_dashboard')",
            login='hd_ui_employee', timeout=120)


@tagged('post_install', '-at_install')
class TestDragCloseStampsDate(TransactionCase):

    def test_drag_to_closed_stage_sets_and_clears_close_date(self):
        Stage = self.env['helpdesk.ticket.stage']
        closed = Stage.search([('is_closed', '=', True)], limit=1)
        open_stage = Stage.search([('is_closed', '=', False)], order='sequence', limit=1)
        ticket = self.env['helpdesk.ticket'].create({
            'name': 'Drag me', 'description': '<p>x</p>',
            'employee_id': self.env['hr.employee'].create({'name': 'Drag Caller'}).id,
            'category_id': self.env['helpdesk.ticket.category'].search(
                [('ticket_type', '=', 'incident')], limit=1).id,
        })
        ticket.stage_id = closed
        self.assertTrue(ticket.close_date)
        self.assertEqual(ticket.closed_by, self.env.user)
        ticket.stage_id = open_stage
        self.assertFalse(ticket.close_date)
        self.assertFalse(ticket.closed_by)
