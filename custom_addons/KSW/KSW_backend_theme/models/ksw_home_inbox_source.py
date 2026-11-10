import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)


class KswHomeInboxSource(models.Model):
    """One "waiting for me" counter on the Home page.

    Configuration, not code (the SAP "To-Dos" / Dynamics count tile idea):
    a model and a domain. The count runs AS the user, so record rules decide
    what each person sees; a source whose model is not installed, or that the
    user cannot read, is skipped. Only non-zero counters are shown, which is
    what keeps Home from becoming a 70-tile launchpad.
    """
    _name = 'ksw.home.inbox.source'
    _description = 'Home: Waiting For Me Counter'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    model_name = fields.Char(
        string='Model', required=True,
        help='Technical model name, e.g. hr.leave. Ignored while that module is not installed.')
    domain = fields.Char(
        default='[]', required=True,
        help='Records waiting for the current user. Evaluated with uid, user and today.')
    icon = fields.Char(default='fa-inbox', help='Font Awesome class, e.g. fa-plane.')
    group_ids = fields.Many2many(
        'res.groups', string='Only For',
        help='Leave empty to try it for everyone (access rights still apply).')

    @api.constrains('domain')
    def _check_domain(self):
        for source in self:
            try:
                value = safe_eval(source.domain, source._eval_context())
            except Exception as exc:  # noqa: BLE001 - surfaced to the admin as-is
                raise ValidationError(self.env._('Invalid domain: %s', exc)) from exc
            if not isinstance(value, (list, tuple)):
                raise ValidationError(self.env._('The domain must be a list.'))

    def _eval_context(self):
        return {
            'uid': self.env.uid,
            'user': self.env.user,
            'today': fields.Date.context_today(self),
        }

    def _visible_to_user(self):
        user = self.env.user
        return self.filtered(lambda s: not s.group_ids or (s.group_ids & user.all_group_ids))

    @api.model
    def _app_for_model(self, model_name):
        """The app whose menus open `model_name` most often, among the menus this
        user can see; so the top bar shows that app's menus, not the last one used."""
        Menu = self.env['ir.ui.menu']
        actions = self.env['ir.actions.act_window'].sudo().search([('res_model', '=', model_name)])
        if not actions:
            return False
        visible = Menu._visible_menu_ids()
        menus = Menu.sudo().search([
            ('action', 'in', [f'ir.actions.act_window,{a}' for a in actions.ids]),
        ]).filtered(lambda m: m.id in visible)
        hits = {}
        for menu in menus:
            root = int(menu.parent_path.split('/')[0])
            hits[root] = hits.get(root, 0) + 1
        if not hits:
            return False
        roots = Menu.sudo().browse(list(hits))
        return max(roots, key=lambda r: (hits[r.id], -r.sequence, -r.id)).id

    def _counters(self):
        """[{id, name, icon, count, app_id, action}] for the current user, non-zero only."""
        result = []
        for source in self._visible_to_user():
            Model = self.env.get(source.model_name)
            if Model is None or not Model.has_access('read'):
                continue
            try:
                domain = safe_eval(source.domain, source._eval_context())
                count = Model.search_count(domain)
            except Exception:  # noqa: BLE001 - one bad source must not break Home
                _logger.warning('Home counter %s (%s) failed', source.id, source.model_name, exc_info=True)
                continue
            if not count:
                continue
            result.append({
                'id': source.id,
                'name': source.name,
                'icon': source.icon or 'fa-inbox',
                'count': count,
                'app_id': self._app_for_model(source.model_name),
                'action': {
                    'type': 'ir.actions.act_window',
                    'name': source.name,
                    'res_model': source.model_name,
                    'domain': domain,
                    'views': [[False, 'list'], [False, 'kanban'], [False, 'form']],
                    'target': 'current',
                },
            })
        return result
