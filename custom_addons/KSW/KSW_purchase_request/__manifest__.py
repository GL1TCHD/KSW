{
    'name': 'KSW Purchase Request Step',
    'version': '19.0.1.0.0',
    'author': 'Mohammed Albadr',
    'category': 'Inventory/Purchase',
    'summary': 'Purchase Request (PR) before RFQ, switchable per company from the Purchase settings',
    'description': """
Adds OCA Purchase Requests as the first step of the purchase flow:
PR → RFQ → PO → receipt → vendor bill.

A checkbox in Purchase ▸ Configuration ▸ Settings («Require Purchase Request»)
switches the step on or off:

* On: a purchase order can only be confirmed when every product line comes
  from an approved purchase request. The Purchase Requests menus are shown.
* Off: purchase orders are confirmed directly from an RFQ, as in standard
  Odoo, and the Purchase Requests menus are hidden.
""",
    'depends': ['purchase_request'],
    'data': [
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'license': 'LGPL-3',
}
