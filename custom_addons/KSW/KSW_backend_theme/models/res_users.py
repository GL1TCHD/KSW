import json

from odoo import api, fields, models
from odoo.exceptions import AccessError

DEFAULT_CONFIG_PARAM = 'KSW_backend_theme.home_default_config'


def _clean_config(config):
    """Keep only {order: [xmlid], hidden: [xmlid]}: xml ids, so a layout made
    on one database means the same apps on another."""
    config = config if isinstance(config, dict) else {}
    as_list = lambda key: [str(x) for x in (config.get(key) or []) if x]  # noqa: E731
    return {'order': as_list('order'), 'hidden': as_list('hidden')}


class ResUsers(models.Model):
    _inherit = 'res.users'

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + ['ksw_home_config', 'ksw_text_size', 'ksw_tour_done']

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + ['ksw_home_config', 'ksw_text_size']

    ksw_text_size = fields.Selection(
        selection=[
            ('auto', 'Automatic (by language)'),
            ('standard', 'Standard'),
            ('large', 'Large'),
        ],
        string='Text Size',
        default='auto',
        required=True,
        help='Automatic: large for Arabic, standard for English.',
    )

    def _ksw_text_large(self):
        self.ensure_one()
        if self.ksw_text_size == 'auto':
            return (self.lang or '').startswith('ar')
        return self.ksw_text_size == 'large'

    def preference_save(self):
        # Core only reloads the context; the text size is a class on <body>,
        # set when the page is built, so the page itself must reload.
        super().preference_save()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # The 4-step quick tour (static/src/tour): shown once per account, until the
    # user finishes or skips it. Replayable from the user menu.
    ksw_tour_done = fields.Boolean(string='Quick Tour Seen', copy=False)

    @api.model
    def ksw_tour_mark_done(self):
        self.env.user.sudo().ksw_tour_done = True
        return True

    # JSON {order, hidden}; empty = follow the company default.
    ksw_home_config = fields.Text(string='Home Layout', copy=False)

    @api.model
    def _ksw_home_default_config(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(DEFAULT_CONFIG_PARAM)
        try:
            return _clean_config(json.loads(raw)) if raw else _clean_config({})
        except ValueError:
            return _clean_config({})

    @api.model
    def ksw_home_data(self):
        """Everything Home shows, in one round trip."""
        user = self.env.user
        personal = None
        if user.ksw_home_config:
            try:
                personal = _clean_config(json.loads(user.ksw_home_config))
            except ValueError:
                personal = None
        default = self._ksw_home_default_config()
        today = fields.Date.context_today(self)
        Activity = self.env['mail.activity']
        mine = [('user_id', '=', user.id)]
        return {
            'user_name': user.name,
            'counters': self.env['ksw.home.inbox.source'].search([])._counters(),
            'activities': {
                'overdue': Activity.search_count(mine + [('date_deadline', '<', today)]),
                'today': Activity.search_count(mine + [('date_deadline', '=', today)]),
                'planned': Activity.search_count(mine + [('date_deadline', '>', today)]),
            },
            'config': personal or default,
            'is_personal': bool(personal),
            'can_set_default': user.has_group('base.group_system'),
        }

    @api.model
    def ksw_home_save_config(self, config):
        self.env.user.write({'ksw_home_config': json.dumps(_clean_config(config))})
        return True

    @api.model
    def ksw_home_reset_config(self):
        self.env.user.write({'ksw_home_config': False})
        return self._ksw_home_default_config()

    @api.model
    def ksw_home_save_default(self, config):
        """The layout everyone without a personal one gets (Settings admins only)."""
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(self.env._('Only administrators can set the default Home layout.'))
        self.env['ir.config_parameter'].sudo().set_param(
            DEFAULT_CONFIG_PARAM, json.dumps(_clean_config(config)))
        return True
