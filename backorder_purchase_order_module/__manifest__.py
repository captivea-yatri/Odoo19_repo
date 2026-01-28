
{
    'name': 'Backorder Purchase Order',
    'version': '19.0.0.0',
    'category': 'Purchases',
    'summary': 'Manage backorder purchase orders',
    'author': 'Captivea',
    'depends': ['base','purchase', 'stock','account','stock_account'],
    'data': [
        'security/ir.model.access.csv',
        'data/ir.sequence.xml',
        'views/action_menu.xml',
        'views/backorder_purchase_order_views.xml',
        'views/stock_move_view.xml',
        'wizard/update_order_line_wiz_view.xml',

    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
