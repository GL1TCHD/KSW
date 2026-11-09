from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class KswWorkshop(models.Model):
    """One physical workshop: Nabiya (the main one), Tabuk, Al-Hayat.

    A request is routed to a workshop by the department of the supervisor
    whose truck it is (hr.department.x_workshop_id, see
    ksw.workshop.request._workshop_for_requester), and a workshop's managers
    and technicians reach only its requests (res.users._ksw_workshop_scope_ids,
    read by the record rules). The request form itself does not change.

    Deliberately thin, like the postponed multi-location design of 2026-08-20
    (KSW-Brain "Workshop Multiple Locations (Postponed)"): a routing and scope
    key, not a stock location.
    """
    _name = 'ksw.workshop'
    _description = 'Workshop'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(help='Short code, e.g. NAB, TBK, HYT.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_main = fields.Boolean(
        string='Main Workshop',
        help='Requests whose department has no home workshop go here. Exactly one '
             'workshop is the main one.')
    # Same relation as res.users.x_workshop_ids, seen from this side, so an
    # administrator can staff a workshop from either form.
    user_ids = fields.Many2many(
        'res.users', 'ksw_workshop_user_rel', 'workshop_id', 'user_id',
        string='Managers & Staff', domain=[('share', '=', False)],
        help="Workshop managers and technicians who work here. A manager triages, and is "
             "notified of, only his own workshops' requests. Staff named on no workshop "
             "belong to the main one.")
    department_ids = fields.One2many(
        'hr.department', 'x_workshop_id', string='Departments Served')
    technician_ids = fields.One2many(
        'ksw.workshop.technician', 'workshop_id', string='Technicians')
    request_count = fields.Integer(compute='_compute_request_count')

    _name_uniq = models.Constraint('unique(name)', 'A workshop with this name already exists.')

    @api.constrains('is_main', 'active')
    def _check_one_main(self):
        mains = self.search([('is_main', '=', True)])
        if len(mains) > 1:
            raise ValidationError(_('Only one workshop can be the main workshop.'))

    def _compute_request_count(self):
        counts = dict(self.env['ksw.workshop.request']._read_group(
            [('workshop_id', 'in', self.ids)], ['workshop_id'], ['__count']))
        for workshop in self:
            workshop.request_count = counts.get(workshop, 0)

    @api.model
    def _main(self):
        Workshop = self.sudo()
        return Workshop.search([('is_main', '=', True)], limit=1) or Workshop.search([], limit=1)

    @api.model
    def _for_department(self, department):
        """The department's home workshop, else its nearest parent's, else the
        main one. Sub-departments inherit from their parent, so only the top
        department of a branch needs setting."""
        department = department.sudo()
        while department:
            if department.x_workshop_id.active:
                return department.x_workshop_id
            department = department.parent_id
        return self._main()

    # Rule domains are cached per user; staffing a workshop changes them.
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        self.env.registry.clear_cache()
        return records

    def write(self, vals):
        res = super().write(vals)
        if {'user_ids', 'active'} & vals.keys():
            self.env.registry.clear_cache()
        return res

    def action_view_requests(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('KSW_workshop.action_ksw_workshop_request_all')
        action['domain'] = [('workshop_id', '=', self.id)]
        action['context'] = {'create': False}
        return action


class HrDepartment(models.Model):
    _inherit = 'hr.department'

    x_workshop_id = fields.Many2one(
        'ksw.workshop', string='Home Workshop', ondelete='restrict',
        help="The workshop that repairs this department's vehicles. Sub-departments "
             "without one follow their parent; with none at all, the main workshop.")
