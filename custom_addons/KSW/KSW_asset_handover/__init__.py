from . import models

# KSW_helpdesk menus that views/menu.xml moves under the IT Assets app, and
# the Helpdesk parent each one goes back to. ir.ui.menu.parent_id is
# ondelete='restrict', so without this the app root could not be deleted.
_MOVED_MENUS = {
    'menu_helpdesk_assets_all': 'menu_helpdesk_assets',
    'menu_helpdesk_assets_assignments': 'menu_helpdesk_assets',
    'menu_helpdesk_assets_maintenance': 'menu_helpdesk_assets',
    'menu_helpdesk_assets_consumables': 'menu_helpdesk_assets',
    'menu_helpdesk_assets_consumable_moves': 'menu_helpdesk_assets',
    'menu_helpdesk_assets_purchase_requests': 'menu_helpdesk_assets',
    'menu_helpdesk_asset_categories': 'menu_helpdesk_config',
    'menu_helpdesk_consumable_categories': 'menu_helpdesk_config',
}


def uninstall_hook(env):
    for menu_xmlid, parent_xmlid in _MOVED_MENUS.items():
        menu = env.ref(f'KSW_helpdesk.{menu_xmlid}', raise_if_not_found=False)
        parent = env.ref(f'KSW_helpdesk.{parent_xmlid}', raise_if_not_found=False)
        if menu and parent:
            menu.parent_id = parent
