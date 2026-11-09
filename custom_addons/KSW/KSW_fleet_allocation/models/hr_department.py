from odoo import _, api, fields, models


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    x_fleet_holder = fields.Boolean(
        string='Holds Vehicles',
        help='This department is given vehicles and may release or request them.')
    x_fleet_vehicle_ids = fields.One2many('ksw.fleet.vehicle', 'x_department_id', string='Vehicles')
    x_fleet_vehicle_count = fields.Integer(compute='_compute_fleet_vehicle_count', string='Vehicle Count')

    # Overview card figures. Counted through sudo: the card only ever shows
    # the user's own departments (x_fleet_in_my_scope), the GM's all.
    x_fleet_incoming_count = fields.Integer(compute='_compute_fleet_overview', string='On the Way In')
    x_fleet_outgoing_count = fields.Integer(compute='_compute_fleet_overview', string='On the Way Out')
    x_fleet_waiting_count = fields.Integer(compute='_compute_fleet_overview', string='Waiting for GM')
    x_fleet_draft_count = fields.Integer(compute='_compute_fleet_overview', string='Drafts')
    x_fleet_type_summary = fields.Char(compute='_compute_fleet_overview', string='Fleet')
    x_fleet_in_my_scope = fields.Boolean(compute='_compute_fleet_in_my_scope',
                                         search='_search_fleet_in_my_scope')

    def _compute_fleet_overview(self):
        Move = self.env['ksw.fleet.move'].sudo()
        Request = self.env['ksw.fleet.request'].sudo()
        Vehicle = self.env['ksw.fleet.vehicle'].sudo()
        types = dict(Vehicle._fields['vehicle_type'].selection)

        def count(model, domain, field):
            return dict(model._read_group(domain + [(field, 'in', self.ids)], [field], ['__count']))

        incoming = count(Move, [('state', '=', 'in_transfer')], 'to_department_id')
        outgoing = count(Move, [('state', '=', 'in_transfer')], 'from_department_id')
        waiting = count(Request, [('state', '=', 'submitted')], 'department_id')
        drafts = count(Request, [('state', '=', 'draft')], 'department_id')
        by_type = {}
        for department, vehicle_type, n in Vehicle._read_group(
                [('x_department_id', 'in', self.ids)], ['x_department_id', 'vehicle_type'], ['__count']):
            by_type.setdefault(department, []).append('%s %s' % (n, types.get(vehicle_type, '')))
        for department in self:
            department.x_fleet_incoming_count = incoming.get(department, 0)
            department.x_fleet_outgoing_count = outgoing.get(department, 0)
            department.x_fleet_waiting_count = waiting.get(department, 0)
            department.x_fleet_draft_count = drafts.get(department, 0)
            department.x_fleet_type_summary = ' · '.join(by_type.get(department, []))

    @api.depends_context('uid')
    def _compute_fleet_in_my_scope(self):
        sees_all = self.env['ksw.fleet.vehicle']._ksw_fleet_sees_all()
        scope = set(self.env.user._ksw_fleet_department_ids())
        for department in self:
            department.x_fleet_in_my_scope = sees_all or department.id in scope

    def _search_fleet_in_my_scope(self, operator, value):
        # Odoo 19 hands a boolean condition over as in/not in (gotcha #24).
        if operator in ('in', 'not in'):
            wanted = (operator == 'in') == any(value)
        elif operator in ('=', '!='):
            wanted = (operator == '=') == bool(value)
        else:
            return NotImplemented
        if self.env['ksw.fleet.vehicle']._ksw_fleet_sees_all():
            return [] if wanted else [('id', '=', False)]
        return [('id', 'in' if wanted else 'not in', self.env.user._ksw_fleet_department_ids())]

    def _ksw_fleet_action(self, xmlid, domain, context=None):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(xmlid)
        action['domain'] = domain
        action['context'] = context or {}
        action['display_name'] = '%s: %s' % (self.name, action['name'])
        return action

    def action_fleet_incoming(self):
        return self._ksw_fleet_action('KSW_fleet_allocation.action_ksw_fleet_move',
                                      [('to_department_id', '=', self.id), ('state', '=', 'in_transfer')])

    def action_fleet_outgoing(self):
        return self._ksw_fleet_action('KSW_fleet_allocation.action_ksw_fleet_move',
                                      [('from_department_id', '=', self.id), ('state', '=', 'in_transfer')])

    def action_fleet_requests(self):
        return self._ksw_fleet_action('KSW_fleet_allocation.action_ksw_fleet_request',
                                      [('department_id', '=', self.id)],
                                      {'search_default_filter_open': 1})

    def action_fleet_new_release(self):
        return self._ksw_fleet_new_request('release')

    def action_fleet_new_addition(self):
        return self._ksw_fleet_new_request('addition')

    def _ksw_fleet_new_request(self, request_type):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Release Vehicles') if request_type == 'release' else _('Request Vehicles'),
            'res_model': 'ksw.fleet.request',
            'view_mode': 'form',
            'target': 'current',
            'context': {'default_request_type': request_type, 'default_department_id': self.id},
        }

    @api.depends('x_fleet_vehicle_ids')
    def _compute_fleet_vehicle_count(self):
        counts = dict(self.env['ksw.fleet.vehicle']._read_group(
            [('x_department_id', 'in', self.ids)], ['x_department_id'], ['__count']))
        for department in self:
            department.x_fleet_vehicle_count = counts.get(department, 0)

    def _ksw_fleet_holder(self):
        """The vehicle-holding department this one belongs to: itself, else its
        nearest flagged parent. Empty when none up the tree holds vehicles."""
        self.ensure_one()
        department = self.sudo()
        while department:
            if department.x_fleet_holder:
                return department
            department = department.parent_id
        return self.browse()

    def _ksw_fleet_users(self):
        """Users who answer for this department's vehicles: named on it or on
        any department above it."""
        self.ensure_one()
        ancestors = [int(i) for i in (self.sudo().parent_path or '').split('/') if i]
        return self.env['res.users'].sudo().search([('x_fleet_department_ids', 'in', ancestors)])

    def action_open_fleet_vehicles(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id(
            'KSW_fleet_allocation.action_ksw_fleet_allocation_vehicles')
        action['domain'] = [('x_department_id', 'child_of', self.id)]
        action['context'] = {}
        return action
